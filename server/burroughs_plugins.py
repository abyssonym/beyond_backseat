import re
from datetime import datetime
from time import time
from string import ascii_letters
from random import choice, randint
from backseat_server import Server


class Base:
    regex = '$.'
    cooldown = 4

    def match(self, msg):
        if isinstance(self.regex, str):
            self.regex = re.compile(self.regex, re.I)

        matched = self.regex.match(msg)
        if matched:
            return list(matched.groups())

    def execute(self, *args, **kwargs):
        pass

    def respond(self, bot, channel, msg, override_cooldown=False):
        now = time()
        if not override_cooldown:
            if hasattr(bot, 'responded') and bot.responded:
                return
            if hasattr(self, 'last_response'):
                if now - self.last_response < self.cooldown:
                    return
            if (hasattr(bot, 'last_global_response') and
                    hasattr(bot, 'global_cooldown')):
                if now - bot.last_global_response < bot.global_cooldown:
                    return
        bot.responded = True
        if msg:
            bot.msg(channel, msg)
        self.last_response = time()
        bot.last_global_response = self.last_response

    def run(self, bot, user, channel, msg):
        if channel == bot.nickname:
            channel = user
            if msg[:len(bot.nickname)] != bot.nickname:
                msg = '%s: %s' % (bot.nickname, msg)

        parameters = self.match(msg)
        if isinstance(parameters, list):
            self.say = lambda s: self.respond(bot, channel, s)
            self.pm = lambda s: self.respond(bot, user, s)
            self.execute(*parameters)


class Logger(Base):
    logname = 'logs.txt'
    output = open(logname, 'a+', buffering=1)

    def execute(self, channel, user, msg):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.output.write('%s %s %s: %s\n' % (timestamp, channel, user, msg))

    def run(self, bot, user, channel, msg):
        self.execute(channel, user, msg)


class Greetings(Base):
    greetings = ['hi', 'hello', 'hey', 'sup', 'yo']
    appropriate = {'ping': 'pong',
                   'marco': 'polo',
                   'annyong': 'annyong',
                   }

    def __init__(self, nicknames):
        self.nicknames = nicknames

    def match(self, msg):
        msg = msg.lower()
        #msg = ''.join([c if c in ascii_letters else ' ' for c in msg])
        temp = [w for w in msg.split() if w.strip()]
        msg = []
        for m in temp:
            sub = m
            while sub and sub[-1] not in ascii_letters:
                sub = sub[:-1]
            if sub in self.nicknames:
                msg.append(sub)
            else:
                sub = ''.join([c if c in ascii_letters else ' ' for c in m])
                if ' ' in sub:
                    sub = sub.split()
                    sub = [s.strip() for s in sub if s.strip()]
                    msg.extend(sub)
                else:
                    msg.append(sub)
        for nickname in self.nicknames:
            if len(msg) == 2 and nickname in msg:
                word = [w for w in msg if w != nickname][0]
                if (word in self.greetings or
                        word in list(self.appropriate)):
                    return word

    def execute(self, user, word):
        if randint(1, 100) == 100:
            self.say("/me pretends that she didn't hear anything.")
            return

        if word in self.appropriate:
            response = self.appropriate[word]
        else:
            response = choice(self.greetings)

        if choice([True, False]):
            response = response[0].upper() + response[1:]

        punctuation = choice(['!', '', '.'])

        template = choice(['{0}: {1}{2}', '{1}, {0}{2}', '{1} {0}{2}'])
        self.say(template.format(user, response, punctuation))

    def run(self, bot, user, channel, msg):
        if channel == bot.nickname:
            channel = user
            if msg[:len(bot.nickname)] != bot.nickname:
                msg = '%s: %s' % (bot.nickname, msg)

        word = self.match(msg)
        if word:
            self.say = lambda s: self.respond(bot, channel, s)
            self.execute(user, word)


class Confusion(Base):
    expressions = ['huh?', 'what?', 'eh?', 'pardon?', 'come again?',
                   'sorry, what?', 'excuse me?', 'I beg your pardon?',
                   'could you run that by me again?', 'you what?',
                   'What did you just call me?', 'uh...']
    cooldown = 60

    def __init__(self, nicknames):
        self.regex = r'.*(%s)' % '|'.join(nicknames)

    def run(self, bot, user, channel, msg):
        if channel == bot.nickname:
            channel = user
            if msg[:len(bot.nickname)] != bot.nickname:
                msg = '%s: %s' % (bot.nickname, msg)

        parameters = self.match(msg)
        if isinstance(parameters, list):
            self.say = lambda s: self.respond(bot, channel, s)
            self.pm = lambda s: self.respond(bot, user, s)
            self.execute(*parameters)

    def execute(self, *args, **kwargs):
        response = choice(self.expressions)
        if choice([True, False]):
            response = response[0].upper() + response[1:]
        self.say(response)


class Backseater(Base):
    def __init__(self, nicknames):
        self.nicknames = nicknames
        if not hasattr(Backseater, 'server'):
            Backseater.server = Server()

    @classmethod
    def daemon(self, bot):
        self.server.poll()
        to_join = self.server.get_channels() - bot.channels
        for channel in sorted(to_join):
            bot.join(channel)

    def execute(self, channel, user, msg):
        msg = msg.lower()
        if 'beyond' not in msg:
            return
        for n in self.nicknames + ['!beyond']:
            if n.lower() in msg:
                break
        else:
            return

        a, b = msg.split('beyond', 1)
        for n in sorted(self.nicknames, key=lambda nn: -len(nn)):
            a = a.replace(n, '')
        if set(a) & set('abcdefghijklmnopqrstuvwxyz'):
            return

        b = b.strip()
        self.server.delegate_command(channel, user, b)
        return True

    def run(self, bot, user, channel, msg):
        result = self.execute(channel, user, msg)
        if result:
            bot.responded = True
