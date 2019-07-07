import logging
import threading
import socket
import sys
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import random

########### constants

MULTICAST_ADDR = "239.255.4.3"
ANNOUNCE_ALIVE_DELAY = 30 # time (in s) between advertizing, must be >=1 s)
TIMEOUT=1.5

udpPort=3235
tcpPortServer=4112

DEST_ALL="toAll" # sending a message to everyone

# TCP tags
TERM="/EOL"
SEP=":,:"
QUESTIONTAG="QuEsTiOn"
ANSWERTAG="AnSwEr"
MESSAGETAG= "MeSsAgE"
PINGTAG= "PiNg"


# udp advertizing tags
MSG_ALIVE = "alive"
MSG_HELLO = "hello"
MSG_QUIT_TCP="quitQUITquit"


def _fr_template(self, message, sender):
    """
    Function provided by the user to deal with any received message.
    This function is called from a threadpoolExecutor and block connect library
    :param message: message received
    :param sender: name of the device which sent the message
    :return: nothing
    """
    logging.info("message received from " + str(sender) + " : " + message)

def _fhu_template(self, clientDict):
    """
    Function provided by the user to deal with any change in the remote devices list
    :param clientDict: dict containing names and ip adresses of remote devices available
    A client not listed here is not available. However, a client listed here might have been disconnected before.
    :return: nothing
    """
    pass

def _fe_template(self, error):
    """
    Function provided by the user to deal with any error occurred while dealing with a message/question
    :param error: object representing the error which occurred
    :return: nothing
    """
    logging.warning(str(error))

def _fa_template(self, question, sender):
    """
    Function provided by the user
    :param question: question received
    :param sender: name of the remote device which sent the question and wait for an answer
    :return: the answer to the question (a str object)
    """
    return ""



################# FORMATING #################

def _format( ins):
    return (ins+TERM).encode('utf8')

def _decode( ins):
    clear=ins.decode('utf8')
    if TERM in clear:
        return clear.split(TERM)[0] # any following messages is dropped
    else:
        return -1

############### MAIN CLASS ##################


class Connect():

    instance=None

    def __init__(self, fr=_fr_template, fhu=_fhu_template, fe=_fe_template, fa=_fa_template, name=None,
                 multicast_ip=MULTICAST_ADDR, port=udpPort):
        """
        This class enable TCP connection of several devices which do not know each other ad wether they are connected or not
        The devices must a least connect to the same multicast ip adress and port (provided the multicast adress is valid)
        The following parameters are all optional but for optimal use, fr and name must at least be provided.

        :param fr: function called when a user message is received, (see _fr_template)
        :param fhu: function called and the list of remote devices has changed (see _fhu_template)
        :param fe: function called when an error occured while calling fr or fa (see _fe_template)
        :param fa: function called when a answer is awaited to a question asked by a remote device (see _fa_template)
        :param name: name of the node (if not provided, tha name of the host is used instead
        :param multicast_ip: ip used for multicast from 239.0.0.0 to 239.255.255.252, default is MULTICAST_ADDR value
        (these addresses are Administratively scoped and intented to be used on local networks only)
        :param port: port used to receive message (port+1 is used to send them)
        """
        Connect.instance = self

        self.multicast_ip = multicast_ip
        self.port = port
        self.myfuncRecep = fr
        self.myfuncError = fe
        self.myfuncAnswer = fa
        self.funcHostsUpdate = fhu

        self.alive = True
        self.answerLock = threading.Lock()
        self.answerAwaitedFrom = {}  ######### used for question/answers
        self.remoteDevices = {}

        if name is not None:
            self.name = name
        else:
            self.name = socket.gethostname()

        # start tcp comm
        self.tcpThread = threading.Thread(target=self._dealWithMessaging)
        self.tcpThread.name = "TCP thread"
        self.tcpThread.setDaemon(True)
        self.tcpThread.start()

        # start UDP
        self.udpThread=None
        self._restartUDP()


    # functions which may be called by the user
    def leavingNetwork(self):
        if self.my_UdpSocket:
            self.my_UdpSocket.settimeout(0.05)

        self.alive=False

    def askDevice(self, question, dest,timeout=2):
        """
        function is used to ask something to a remote and wait for its answer.
        This function may block the thread up to timout second !

        :param question: what is asked to the remote device
        :param dest: remote device name (or list, but not DEST_ALL)
        :param timeout: max amount of time to wait for the answers.
        :return: the answer(s) of the remote(s) device(s)
        """

        uniqueKey=random.randint(1,9999) # unique key used to track the answers for the question

        if not isinstance(dest, list):
            dest = [dest]

        with self.answerLock:
            self.answerAwaitedFrom[uniqueKey] = {}
            for d in dest:
                self.answerAwaitedFrom[uniqueKey][d] = ""
                self._tcpSend_from_outside(d,QUESTIONTAG+SEP+str(uniqueKey)+SEP+ question)

        def answersAllArived():
            result = True
            with self.answerLock:
                for key, value in self.answerAwaitedFrom[uniqueKey].items():
                    if value == "" :
                        result = False
            return result

        count = 0
        wait = 0.005
        while not answersAllArived() and count * wait < timeout:
            time.sleep(wait)
            count = count+1

        with self.answerLock:
            output = copy.deepcopy(self.answerAwaitedFrom[uniqueKey])

        logging.debug("waiting for answer during " + str(count * wait) + " s")
        with self.answerLock:
            del self.answerAwaitedFrom[uniqueKey]

        return output

    def sendMessage(self, message,dest):
        """
        Sending a message to a dest (defined by its name) OR a list of dest OR all dests
        :param dest: name or list of names of DEST_ALL tag of remote devices to send the message to
        :param message: message (not encoded) to be sent
        :return: nothing
        """
        if not dest == DEST_ALL:
            if not isinstance(dest, list):
                dests = [dest]
            else:
                dests = dest
        else:
            dests = self.remoteDevices.keys()

        for name in dests:
            self._tcpSend_from_outside(name, MESSAGETAG + SEP + message)

    # internal functions

    def _answerDevice(self,uniqueKey,device,question):
        answer = self.myfuncAnswer(question,device)
        self._tcpSend_from_outside(device,ANSWERTAG+SEP+str(uniqueKey)+SEP+answer)

    def _isConnected(self,name):
        return name in self.remoteDevices

    def _removeRemoteDevice(self, name):
        if self._isConnected(name):

            self.remoteDevices[name].writer.write(_format(MSG_QUIT_TCP)) ########## not sure about it

            self.remoteDevices[name].writer.close()

            del self.remoteDevices[name]
            self.funcHostsUpdate(self.remoteDevices)
            logging.info("connection stopped with " + str(name))

    def _addRemoteDevice(self, addr, name,connect=False):
        self.remoteDevices[name] = _remoteDevice(name,addr)
        # start tcp connection
        self.funcHostsUpdate(self.remoteDevices)

        if connect:
            asyncio.run_coroutine_threadsafe(self._connectToRemoteDevice(addr, name), self.asyncioLoop)

    ###############################################
    ############# UDP advertizing #################
    ###############################################

    def _restartUDP(self):
        # start udp multicast
        if  self.udpThread:
            self.udpAlive = False
            self.my_UdpSocket.settimeout(0.05)
            if self.udpThread.isAlive():
                self.udpThread.join()

        self.udpAlive = True
        self.udpThread = threading.Thread(target=self._dealWithAdvertizing)
        self.udpThread.name = "UDP thread"
        self.udpThread.setDaemon(True)
        self.udpThread.start()

    def _sendUDP(self, kind, mess):
        self.my_UdpSocket.sendto((kind + SEP + mess).encode(), (self.multicast_ip, self.port))

    def _dealWithAdvertizing(self):
        self.my_UdpSocket = self._create_socket(self.multicast_ip, self.port)

        lastSent = time.time()

        # message sent two times (safer)
        self._sendUDP(MSG_HELLO, self.name)
        time.sleep(0.02)
        self._sendUDP(MSG_ALIVE, self.name)

        while self.alive and self.udpAlive :

            # announcing
            now = time.time()
            if now - lastSent > ANNOUNCE_ALIVE_DELAY:
                self._sendUDP(MSG_ALIVE, self.name)
                lastSent = now

            # listening
            try :

                data, address = self.my_UdpSocket.recvfrom(256)  # TODO : to be improved
                data = data.decode()
                kind, name = data.split(SEP)
                now=time.time()
                logging.debug("received HRU from " + name)

                if name == self.name:
                    # we don't want to talk with ourself
                    continue

                if kind == MSG_ALIVE or MSG_HELLO:
                    # check if not connected yet

                    if not self._isConnected(name):
                        self._sendUDP(MSG_ALIVE, self.name)
                        self._addRemoteDevice(address,name,name > self.name)
                        self.remoteDevices[name]._seen(now)
                    else :
                        self.remoteDevices[name].addr=address
                        self.remoteDevices[name]._seen(now)

            except socket.timeout:
                pass


        self.my_UdpSocket.close()

    def _get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    def _create_socket(self, multicast_ip, port):
        """
        Creates a socket, sets the necessary options on it, then binds it. The socket is then returned for use.
        """
        local_ip = self._get_local_ip()

        # create a UDP socket
        my_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        my_socket.settimeout(TIMEOUT)  # wait no more than 1,5 second

        # allow reuse of addresses
        my_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # set multicast interface to local_ip
        my_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))

        # Set multicast time-to-live to 3...should keep our multicast packets from escaping the local network
        my_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 3)

        # Construct a membership request...tells router what multicast group we want to subscribe to
        membership_request = socket.inet_aton(multicast_ip) + socket.inet_aton(local_ip)

        # Send add membership request to socket
        # See http://www.tldp.org/HOWTO/Multicast-HOWTO-6.html for explanation of sockopts
        my_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership_request)

        # Bind the socket to an interface.
        # If you bind to a specific interface on osx or linux, no multicast data will arrive.
        # If you try to bind to all interfaces on Windows, no multicast data will arrive.
        # Hence the following.
        if sys.platform.startswith("darwin") or sys.platform.startswith("linux"):
            my_socket.bind(('0.0.0.0', port))
        else:
            my_socket.bind((local_ip, port))

        return my_socket









        # asyncio functions

    ###############################################
    ############## TCP messaging ##################
    ###############################################

    ####### functions called from outside asyncio

    def _dealWithMessaging(self):

        self.asyncioloopCreated = False
        self.asyncioClientWriters = {}  # dict to store printers to communicate with other devices
        self.asyncioExecutor = ThreadPoolExecutor(max_workers=1)
        asyncio.run(self._main())

    def _checkRemotesAreStillConnected(self,now):
        """
        Deal with remote devices and check wether they are still connected or not
        :param now:
        :return:
        """
        toClose=[]

        for remoteName in self.remoteDevices.keys():
            remote=self.remoteDevices[remoteName]
            if now-remote.lastSeen() >  ANNOUNCE_ALIVE_DELAY*2:
                self._tcpSend_from_outside(remoteName,PINGTAG)

            elif now-remote.lastSeen() > ANNOUNCE_ALIVE_DELAY *5:
                toClose.append(remoteName)

        for elem in toClose:
            self._removeRemoteDevice(elem)

    def _tcpSend_from_outside(self, name, message, wait=False):
        """
        Send message (helper method for _tcpSendAS) : This message must begin with a tag and a sep
        :param name: device's name
        :param message: message to be sent
        :return:
        """
        if not self._isConnected(name):
            return False

        future = asyncio.run_coroutine_threadsafe(self.remoteDevices[name]._tcpSendAS(message), self.asyncioLoop)
        if wait:
            future.result()
        return True

    def _callback(self, future):
        if future.exception():
            self.myfuncError(future._request)

    ###### async func managed by asyncio

    async def _main(self):
        self.asyncioLoop = asyncio.get_event_loop()
        self.asyncioloopCreated = True

        self.server = await asyncio.start_server(self._handleClient, "", tcpPortServer, start_serving=False)
        async with self.server:
            await self.server.serve_forever()  # this appears to be a blocking call

            # server might be stopped by calling shutdown from ANOTHER thread (else : deadlock)

    async def _handleClient(self, reader, writer):

        # exchange of names
        writer.write(_format(self.name))
        clientName = await _completeRead(reader) # first thing received is the name of the client
        self.asyncioClientWriters[clientName] = writer # writer is store to enable writing from outside of this function
        await writer.drain()

        if clientName not in self.remoteDevices:
            self.remoteDevices[clientName]= _remoteDevice(clientName,reader=reader,writer=writer)
        else:
            self.remoteDevices[clientName]._setReaderWriter(reader,writer)

        remoteClient=self.remoteDevices[clientName]

        while self.alive :
            data= await remoteClient._completeRead()
            if data == -1 or data == MSG_QUIT_TCP:
                # in this case, a problem (disconnection happened, ... )
                break

            logging.debug("Received " +str(data)+ "    from client "+str(clientName))
            content=data.split(SEP)
            kind=content[0]

            if kind == QUESTIONTAG:
                uniqueKey = int(content[1])
                future = self.asyncioLoop.run_in_executor(self.asyncioExecutor, self._answerDevice, uniqueKey, clientName,content[2])
                future._request = data
                future.add_done_callback(self._callback)
                await future

            elif kind == ANSWERTAG:
                uniqueKey=int(content[1])
                with self.answerLock:
                    if uniqueKey in self.answerAwaitedFrom.keys():
                        self.answerAwaitedFrom[uniqueKey][clientName] = content[2]

            elif kind == MESSAGETAG :
                future = self.asyncioLoop.run_in_executor(self.asyncioExecutor, self.myfuncRecep,content[1],clientName)
                future._request=data
                future.add_done_callback(self._callback)
                await future

            elif kind == PINGTAG :
                # in this case, our MSG_ALIVE udp messages are not received anymore by the device called clientName
                # and something must be done, such as restarting the udp stack
                self._restartUDP()
                logging.WARNING("ping received from "+clientName+ " (probably caused by a problem in UDP socket, which has been restarted")


        self._removeRemoteDevice(clientName)

    async def _connectToRemoteDevice(self, addr, name):
        """
        :param addr: IP adress of the client
        :param name: its name (obtained by udp multicast)
        :return: nothing
        """
        reader, writer = await asyncio.open_connection(addr[0], tcpPortServer)
        asyncio.run_coroutine_threadsafe(self._handleClient(reader, writer), self.asyncioLoop)

    async def _closeProperly(self):

        for key, value in self.asyncioClientWriters.items():
            value.write(_format(MSG_QUIT_TCP))
            value.close()
        await self.asyncioLoop.shutdown_asyncgens()
        self.server.close()
        await self.server.wait_closed()


async def _completeRead(reader):
    clear = -1
    data=b""
    count=0
    while clear == -1 and count<4:
        partial = await reader.read(128)
        data =data + partial
        clear=_decode(data)
        count = count+1

    return clear


################ HELPER CLASS ###################


class _remoteDevice():

    def __init__(self,name,addr=None,writer=None,reader=None,lastSeen=None):
        self.name = name
        self.addr = addr
        self.writer = writer
        self.reader = reader
        self.lastSeen = lastSeen
        if not lastSeen:
            self.lastSeen=time.time()

    def _setReaderWriter(self,reader,writer):
        self.reader=reader
        self.writer=writer

    def _seen(self,lastSeen=None):
        if not lastSeen:
            self.lastSeen=time.time()
        else:
            self.lastSeen=lastSeen

    async def _completeRead(self):
        return await _completeRead(self.reader)

    async def _tcpSendAS(self,  message):
        logging.debug("Sending " + str(message) + "    to " + str(self.name))
        self.writer.write(_format(message))
        await self.writer.drain()