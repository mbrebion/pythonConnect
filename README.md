# pythonConnect

### General presentation

This python library aims at easing the connection between devices which do not know each other and wether they are connected or not.
Each device advertizes itself via udp multicast to a dedicated ip address (which is also known/needed by the other devices).

Once an advert is received from another device, a TCP communication is created (using asyncio) the the two devices may send messages, question/answers to each other.

*This library allows the user to forget about UDP/TCP communication and just focus about message passing between devices.*


The main class called Connect must be instanciated by giving at least a name for the device (the name that will be provided to other devices) and functions to deals with incomming messages, incomming questions, changes in the list of available remote devices and errors which occured while dealing with messages/questions. 



### Example of instanciating : 
```
from connect import Connect

# instanciating the network
connect=Connect(fr=receivingMessage, fhu=updatingRemotesList, fe=self.errorHandling,fa=self.answerToQuestion, name=name)

def receivingMessage(message,source):
  # do something (this function is called from a threadpoolexecutor and wont block the network)
  pass
  
def updatingRemotesList(remoteList):
  # this function is called when the list of remote devices has changed. Remote List is a dict which keys are the names of the devices
  # and values are their IP/port
  pass
  
def errorHandling(error):
  #error is the message/question, which lead to a python exception while calling fr or fa
  pass
  
def answerToQuestion(question,source):
  # do something regarding the question asked by the remote device called source
  answer="" # provide an appropriated answer
  return answer
```
### Example of use : 

```
# send a message : 

connect.sendMessage(message,dest):

# dest might be a name of a remote device or a list of name of remote devices


# ask a question

answer = connect.askDevice(question, dest,timeout=3)

# dest might be a name of a remote device or a list of name of remote devices
# timout (2 by default) represent the max amount of time (in second) to wait for the answers
# answer dict which keys are the devices names which were asked and the values, their answers. An ampty string is provided for each remote devices which dit not answered in time
```
### Contributing
As I'm not an expert in networking, any help will be appreciated to improve this library. 
