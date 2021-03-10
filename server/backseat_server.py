import gzip
import socket

POLL_INTERVAL = 0.5
SERVER_IP = '127.0.0.1'
SERVER_PORT = 55333
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.bind((SERVER_IP, SERVER_PORT))
server_socket.settimeout(POLL_INTERVAL)


class Client:
    def __init__(self, address, port, serial_number):
        self.address = address
        self.port = port
        self.serial_number = serial_number
        self.next_command_index = 0
        self.index_commands = []
        self.channel = None
        self.allowed_users = set([])

    @property
    def signature(self):
        return self.address, self.port

    def send(self, msg):
        msg = msg.encode()
        temp = b'!' + gzip.compress(msg)
        if len(temp) < len(msg):
            msg = temp
        assert len(msg) < 4096
        server_socket.sendto(msg, self.signature)

    def process_report(self, msg):
        channel, allowed_users = msg.split(' ', 1)
        allowed_users = [au.strip() for au in allowed_users.split(',')]
        self.channel = channel.strip()
        self.allowed_users = set(allowed_users)

    def get_channel(self):
        self.send('?')

    def add_command(self, command):
        self.index_commands.append((self.next_command_index, command))
        self.next_command_index += 1

    def send_commands(self):
        if self.index_commands:
            msg = ','.join(['{0}-{1}'.format(index, command)
                            for (index, command) in self.index_commands])
            self.send(msg)

    def process_seen(self, msg):
        confirmed = set([int(index.strip()) for index in msg.split(',')])
        self.index_commands = [
            (index, command) for (index, command) in self.index_commands
            if index not in confirmed]

    def process_message(self, msg):
        msg = msg.strip()
        if msg == '?':
            self.send_commands()
        elif msg.startswith('#'):
            self.process_report(msg[1:])
        elif msg.startswith('+'):
            self.process_seen(msg[1:])
        else:
            raise Exception('Unknown message')


class Server():
    clients = []

    @classmethod
    def get_channels(self):
        return {'#{0}'.format(c.channel) for c in self.clients if c.channel}

    def receive(self):
        try:
            msg, (address, port) = server_socket.recvfrom(4096)
        except socket.timeout:
            return None
        if msg[0] == ord('!'):
            msg = gzip.decompress(msg[1:])
        msg = msg.decode('ascii').strip()
        return msg, (address, port)

    def get_client(self, address, port, serial_number):
        for c in self.clients:
            if c.channel is None:
                c.get_channel()
            if (c.address == address and c.port == port
                    and c.serial_number == serial_number):
                return c
        c = Client(address, port, serial_number)
        self.clients.append(c)
        return c

    def poll(self):
        received = self.receive()
        if received is not None:
            msg, (address, port) = received
            serial_number, msg = msg.split(' ', 1)
            client = self.get_client(address, port, serial_number)
            client.process_message(msg)

    def delegate_command(self, channel, user, command):
        channel = channel.lstrip('#')
        for c in self.clients:
            if c.channel == channel and user in c.allowed_users:
                c.add_command(command)
