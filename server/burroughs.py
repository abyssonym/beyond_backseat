from twisted.words.protocols import irc
from twisted.internet import reactor, protocol
from twisted.internet.error import ConnectionLost

from datetime import datetime
from os import environ
from time import sleep
import traceback

from burroughs_plugins import Confusion, Greetings, Logger, Backseater


NICKNAME = "burroughs_exe"
NICKPASSWORD = environ['BURROUGHS_PASSWORD']
CHANNELS = ["#abyssonym"]
RESPOND_TO = ["burroughs_exe", "burroughs", "burroughs.exe"]

PLUGINS = [
    Backseater(RESPOND_TO),
    Greetings(RESPOND_TO),
    Confusion(RESPOND_TO),
    Logger(),
    ]

DAEMONS = [
    Backseater,
    ]

SYSLOG_FILENAME = 'burroughs.log'


def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = '{0} {1}'.format(timestamp, msg)
    print(msg)
    f = open(SYSLOG_FILENAME, 'a+')
    f.write(msg)
    f.close()


class Burroughs(irc.IRCClient):
    nickname = NICKNAME
    password = NICKPASSWORD
    channels = set(CHANNELS)
    cooldown = 3.5

    def signedOn(self):
        self.msg("Q", "auth %s %s" % (self.nickname, self.password))
        sleep(0.5)
        for channel in self.channels:
            self.join(channel)
        self.msg(CHANNELS[0], "Good morning, Master.")
        #if self.password:
        #    self.msg("NickServ", "IDENTIFY %s" % self.password)
        self.repeatingPing(59)
        self.daemon()

    def repeatingPing(self, delay):
        reactor.callLater(delay, self.repeatingPing, delay)
        self.ping(self.nickname)

    def daemon(self):
        reactor.callLater(1, self.daemon)
        for d in DAEMONS:
            try:
                d.daemon(self)
            except Exception:
                log(traceback.format_exc())

    def privmsg(self, user, channel, msg):
        try:
            user, _ = user.split('!', 1)
        except ValueError:
            pass

        self.responded = False
        for p in PLUGINS:
            try:
                p.run(self, user, channel, msg)
            except Exception:
                log(traceback.format_exc())

    def joined(self, channel):
        self.channels.add(channel)

    def left(self, channel):
        self.channels = [c for c in self.channels if c != channel]

    def clientConnectionLost(self, reason):
        log("connection lost: %s" % reason)
        if reactor.running:
            reactor.stop()
        raise ConnectionLost


class BurroughsFactory(protocol.ClientFactory):
    def buildProtocol(self, addr):
        p = Burroughs()
        p.factory = self
        return p

    def clientConnectionLost(self, connector, reason):
        log("lost connection: %s" % reason)
        connector.connect()

    def clientConnectionFailed(self, connector, reason):
        log("connection failed: %s" % reason)
        reactor.stop()


if __name__ == '__main__':
    while True:
        try:
            f = BurroughsFactory()
            reactor.connectTCP("irc.twitch.tv", 6667, f)
            reactor.run()
        except Exception:
            sleep(15)
            continue
        break
