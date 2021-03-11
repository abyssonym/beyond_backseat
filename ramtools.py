import random
import socket
import traceback
from configparser import ConfigParser
from datetime import datetime
from gzip import compress, decompress
from os import _exit, path
from sys import argv
from threading import Thread
from time import sleep, time

try:
    from sys import _MEIPASS
    tblpath = path.join(_MEIPASS, "tables")
except ImportError:
    tblpath = "tables"

try:
    config = ConfigParser()
    if len(argv) > 1:
        config.read(argv[1])
    else:
        config.read('beyond.cfg')

except:
    raise Exception('Configuration file error. ')


UPDATE_INTERVAL = int(config['Misc']['update_interval'])
SERIAL_NUMBER = int(time())
HANDLERS = {}


class classproperty(property):
    def __get__(self, inst, cls):
        return self.fget(cls)


class Logger():
    def __init__(self, filename=None, print_logs=True):
        self.logfile = None
        if filename is not None:
            self.set_logfile(filename)
        self.print_logs = print_logs
        self.unprinted = ''

    def set_logfile(self, filename):
        self.logfile = open(filename, 'a+')

    def log(self, msg):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = '[{0} {1}] {2}'.format(timestamp, SERIAL_NUMBER, msg)
        if self.print_logs:
            print(msg)
        else:
            self.unprinted += msg + '\n'
        if self.logfile is not None:
            self.logfile.write(msg + '\n')
            self.logfile.flush()

    def print_unprinted(self):
        print(self.unprinted.strip())
        self.unprinted = ''


logger = Logger()


def log(msg):
    logger.log(msg)


class ParityClient():
    NUM_RETRIES = 10
    RETRY_INTERVAL = 0.02

    def __init__(self, emulator_address, emulator_port):
        self.emulator_address = emulator_address
        self.emulator_port = int(emulator_port)
        self.emulator_socket = None

    def connect_emulator(self):
        if self.emulator_socket and self.emulator_socket.fileno() >= 0:
            self.emulator_socket.close()
        self.emulator_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.emulator_socket.connect((self.emulator_address,
                                      self.emulator_port))

    def send_emulator(self, address, data):
        assert len(data) > 0
        MAX_WRITE_LENGTH = 4
        while data:
            subdata, data = data[:MAX_WRITE_LENGTH], data[MAX_WRITE_LENGTH:]
            assert len(subdata) <= MAX_WRITE_LENGTH
            s = ' '.join(['{0:0>2X}'.format(d) for d in subdata])
            cmd = 'WRITE_CORE_RAM {0:0>6x} {1}'.format(address, s)
            cmd = cmd.encode()
            self.emulator_socket.send(cmd)
            address += len(subdata)

    def read_emulator(self, address, num_bytes):
        cmd = 'READ_CORE_RAM {0:0>6x} {1}'.format(address, num_bytes)
        for i in range(self.NUM_RETRIES):
            self.emulator_socket.send(cmd.encode())
            expected_length = 21 + (3 * num_bytes)
            try:
                data = self.emulator_socket.recv(expected_length)
            except socket.timeout:
                raise IOError('Emulator not responding.')
            data = data.decode('ascii').strip()
            data = [int(d, 0x10) for d in data.split(' ')[2:]]
            if len(data) == num_bytes and -1 not in data:
                break
            log('Warning: Emulator read error: {0:x} {1}/{2} bytes'.format(
                address, len(data), num_bytes))
            sleep(self.RETRY_INTERVAL * (1.5**i))
        else:
            raise IOError('Emulator read error: {0:x} {1}/{2} bytes'.format(
                address, len(data), num_bytes))
        return data


client = ParityClient(config['Emulator']['address'],
                      config['Emulator']['port'])


class LivePatch():
    def __init__(self, name, patch_filename, force_valid=False):
        self.client = client
        self.patch_filename = patch_filename
        patch_filepath = path.join(tblpath, patch_filename)
        self.patch = {}
        self.backup = {}
        self.validation = {}
        self.definitions = {}
        self.labels = {}
        self.name = name

        validation_flag = False
        previous_address = None
        lenalpha = lambda s: (-len(s), s)
        def verify_nonhex(s):
            return any([c for c in s if c.lower() not in '0123456789abcdef'])

        f = open(patch_filepath)
        for line in f.readlines():
            if '#' in line:
                index = line.index('#')
                line = line[:index]
                assert '#' not in line
            line = line.strip()
            if not line:
                continue

            if 'VALIDATION' in line:
                validation_flag = True
                previous_address = None
                continue

            if validation_flag:
                data = self.validation
            else:
                data = self.patch

            if previous_address is not None:
                address = previous_address + len(data[previous_address])
            else:
                address = None

            if line.startswith('.def'):
                assert not validation_flag
                _, definition, substitution = line.split()
                assert verify_nonhex(definition)
                assert definition not in self.definitions
                assert definition not in self.labels
                self.definitions[definition] = substitution
                continue

            if line.startswith('.label'):
                assert not validation_flag
                _, label = line.split()
                assert verify_nonhex(label)
                assert label not in self.labels
                assert label not in self.definitions
                for l in self.labels:
                    assert self.labels[l] is not None
                self.labels[label] = None
                continue

            if ':' in line:
                addr, code = line.split(':')
            else:
                addr, code = '', line
            while '  ' in code:
                code = code.replace('  ', ' ')
            code = code.strip().split(' ')

            for definition in sorted(self.definitions, key=lenalpha):
                while definition in code:
                    index = code.index(definition)
                    code[index] = self.definitions[definition]

            new_code = []
            for c in code:
                if verify_nonhex(c):
                    new_code.append(c)
                else:
                    c = [int(c[i:i+2], 0x10) for i in range(0, len(c), 2)]
                    new_code.extend(c)
            code = new_code

            if addr:
                address = int(addr, 0x10)

            data[address] = code
            for l in self.labels:
                if self.labels[l] is None:
                    self.labels[l] = address
                    break

            previous_address = address

        f.close()

        for address, code in sorted(self.patch.items()):
            for label in sorted(self.labels, key=lenalpha):
                if label in code:
                    index = code.index(label)
                    jump = self.labels[label] - (address + 2)
                    if jump < 0:
                        jump = 0x100 + jump
                    if not 0 <= jump <= 0xff:
                        raise Exception('Label out of range %x - %s'
                                        % (address, code))
                    code[index] = jump
            if not all([0 <= c <= 0xff for c in code]):
                raise Exception('Syntax error: %s' % self.patch_filename)

        self.validate(force_valid=force_valid)
        self.make_backup()

    def validate(self, force_valid=False):
        for address, code in sorted(self.validation.items()):
            result = self.client.read_emulator(address, len(code))
            if result != code:
                if not force_valid:
                    log('INFO: Patch %s not fresh.' % self.patch_filename)
                    self.write(self.validation)
                    return
                else:
                    raise Exception('Patch `%s` validation failed.'
                                    % self.patch_filename)

    def make_backup(self):
        for address, code in sorted(self.patch.items()):
            result = self.client.read_emulator(address, len(code))
            self.backup[address] = result

    def set_label(self, label, new_data):
        if isinstance(new_data, int):
            new_data = [new_data]

        address = self.labels[label]
        old_data = self.patch[address]
        if isinstance(old_data, int):
            old_data = [old_data]

        assert len(old_data) == len(new_data)
        self.patch[address] = new_data

    def restore_backup(self):
        self.write(self.backup)

    def apply_patch(self):
        self.write(self.patch)

    def write(self, data):
        for address, code in sorted(data.items()):
            if isinstance(code, int):
                code = [code]
            self.client.send_emulator(address, code)


class TableObject():
    def __init__(self, pointer, index):
        self.pointer = pointer
        self.index = index
        self.old_data = {}
        if not hasattr(self.__class__, '_every'):
            self.__class__._every = []
        self.__class__._every.append(self)

    def __repr__(self):
        s = '{0} {1:0>2X}\n'.format(self.__class__.__name__, self.index)
        margin = max([len(attribute) for attribute, _, _ in self.specs])
        for attribute, length, datatype in self.specs:
            ss = '{0:%s} {1:0>%sX}' % (margin, length*2)
            s += ss.format(attribute, getattr(self, attribute))
            if datatype.startswith('bit:'):
                names = datatype[4:].strip().split()
                assert len(names) == 8
                for n in names:
                    if self.get_bit(n):
                        s += ' ' + n.upper()
                    else:
                        s += ' ' + n.lower()
            s += '\n'
        return s.strip()

    @classmethod
    def load_all(cls):
        TABLE_FILE = path.join(tblpath, 'tables_list.txt')
        f = open(TABLE_FILE)
        for line in f.readlines():
            if '#' in line:
                index = line.index('#')
                line = line[:index]
                assert '#' not in line
            line = line.strip()
            if not line:
                continue

            while '  ' in line:
                line = line.replace('  ', ' ')
            class_name, specs_filename, address, number = line.split()
            address = int(address, 0x10)
            number = int(number)
            if class_name == cls.__name__:
                specs_filepath = path.join(tblpath, specs_filename)
                g = open(specs_filepath)
                specs = []
                for line in g.readlines():
                    line = line.strip()
                    if line.count(',') == 2:
                        attribute, length, datatype = line.split(',')
                        length = int(length)
                        specs.append((attribute, length, datatype))
                    else:
                        attribute, misc = line.split(',')
                        try:
                            length = int(misc)
                            specs.append((attribute, length, 'int'))
                        except ValueError:
                            datatype = misc
                            specs.append((attribute, 1, datatype))
                g.close()

                cls.specs = specs
                cls.name_bits()
                full_length = sum([length for _, length, _ in specs])
                for index in range(number):
                    pointer = address + (full_length * index)
                    cls(pointer, index)

                break
        else:
            raise Exception(
                'Unable to find specs file: {0}'.format(cls.__name__))
        f.close()

    @classproperty
    def every(cls):
        return list(cls._every)

    @classmethod
    def get(cls, index):
        for o in cls.every:
            if o.index == index:
                return o
        raise IndexError(
            'Object index not available: {0} {1:0>2X}'.format(cls.__name__,
                                                              index))

    @classmethod
    def name_bits(cls):
        cls.bitnames = {}
        for attribute, length, datatype in cls.specs:
            if datatype.startswith('bit:'):
                assert length == 1
                names = datatype[4:].strip().split()
                assert len(names) == 8
                for i, name in enumerate(names):
                    assert name not in cls.bitnames
                    cls.bitnames[name] = (attribute, i)

    def set_bit(self, name, bitvalue):
        assert bitvalue in [True, False]
        attribute, bitindex = self.bitnames[name]
        value = getattr(self, attribute)
        mask = (1 << bitindex)
        if bool(value & mask) == bitvalue:
            return
        value ^= mask
        setattr(self, attribute, value)
        assert self.get_bit(name) == bitvalue

    def get_bit(self, name):
        attribute, bitindex = self.bitnames[name]
        bitvalue = bool(getattr(self, attribute) & (1 << bitindex))
        return bitvalue

    def read_data(self):
        full_length = sum([length for _, length, _ in self.specs])
        data = client.read_emulator(self.pointer, full_length)
        packed_data = list(data)
        offset = 0
        for attribute, length, datatype in self.specs:
            subdata = data[offset:offset+length]
            assert len(subdata) == length
            if datatype.startswith('bit:'):
                assert len(subdata) == 1

            if datatype == 'int' or datatype.startswith('bit:'):
                value = 0
                for subvalue in reversed(subdata):
                    assert 0 <= subvalue <= 0xff
                    value <<= 8
                    value |= subvalue
                setattr(self, attribute, value)
                self.old_data[attribute] = value
            else:
                raise TypeError('Unknown data type.')
            offset += length

        assert self.packed_data == packed_data

    @property
    def packed_data(self):
        full_length = sum([length for _, length, _ in self.specs])
        data = []
        for attribute, length, datatype in self.specs:
            if datatype == 'int' or datatype.startswith('bit:'):
                if datatype.startswith('bit:'):
                    assert length == 1

                subdata = []
                subvalue = getattr(self, attribute)
                for _ in range(length):
                    subdata.append(subvalue & 0xff)
                    subvalue >>= 8

                assert len(subdata) == length
                data += subdata
            else:
                raise TypeError('Unknown data type')
        assert len(data) == full_length
        assert all([0 <= d <= 0xff for d in data])
        return data

    def write_data(self):
        data = self.packed_data
        client.send_emulator(self.pointer, data)


class BurroughsClient():
    ADDRESS = config['Server']['address']
    PORT = int(config['Server']['port'])
    POLL_INTERVAL = max(int(config['Server']['poll_interval']), 1)

    def __init__(self):
        self.previous_poll = 0
        self.jobs = []
        self.server_socket = None
        self.connect_server()
        self.seen = set([])

    def poll_wait(self):
        now = time()
        delta = now - self.previous_poll
        if delta < self.POLL_INTERVAL:
            sleep(self.POLL_INTERVAL - delta)
        self.previous_poll = now

    def connect_server(self):
        if self.server_socket and self.server_socket.fileno() >= 0:
            self.server_socket.close()
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_socket.connect((self.ADDRESS, self.PORT))
        self.server_socket.settimeout(self.POLL_INTERVAL)

    def send_server(self, msg):
        msg = '{0} {1}'.format(SERIAL_NUMBER, msg)
        msg = msg.encode()
        compressed_msg = b'!' + compress(msg)
        if len(compressed_msg) < len(msg):
            msg = compressed_msg
        self.server_socket.send(msg)

    def listen_server(self):
        msg = self.server_socket.recv(4096)
        if msg and msg[0] == ord('!'):
            msg = decompress(msg[1:])
        msg = msg.decode('ascii').strip()
        return msg

    def poll(self):
        self.poll_wait()
        self.send_server('?')
        try:
            msg = self.listen_server()
            if msg == '?':
                self.report()
            elif msg == '.':
                pass
            elif msg:
                index_commands = [ic.strip() for ic in msg.split(',')]
                to_confirm = []
                for index_command in index_commands:
                    index, command = index_command.split('-',1)
                    to_confirm.append(index)
                    if index in self.seen:
                        continue
                    self.seen.add(index)
                    self.jobs.append(command_to_job(command))
                self.confirm(to_confirm)
        except socket.timeout:
            pass
        if self.jobs:
            j = self.jobs.pop(0)
            return j

    def report(self):
        status = '#{0} {1}'.format(config['Chat']['channel'],
                                   config['Chat']['allowed_users'])
        self.send_server(status)

    def confirm(self, to_confirm):
        msg = '+{0}'.format(','.join(to_confirm))
        self.send_server(msg)


def dispatch_to_job(handler_name, *args, **kwargs):
    handler = HANDLERS['handler_%s' % handler_name]
    return handler(*args, **kwargs)


def command_to_job(command):
    whitelist = [c.strip()
                 for c in config['Misc']['whitelist_commands'].split(',')]
    blacklist = [c.strip()
                 for c in config['Misc']['blacklist_commands'].split(',')]

    if whitelist and '*' not in whitelist and command not in whitelist:
        log('Command %s not whitelisted.' % command)

    if blacklist and command in blacklist or '*' in blacklist:
        log('Command %s blacklisted.' % command)

    try:
        s = config['Commands'][command]
        if ':' in s:
            handler_name, args = s.split(':')
            args = [a.strip() for a in args.split(',')]
            args = [int(a[2:], 0x10) if a.startswith('0x') else
                    int(a) if a.isdigit() else a for a in args]
        else:
            handler_name, args = s, []
        log('Running command: %s %s %s' % (command, handler_name, args))
        return dispatch_to_job(handler_name, command, *args)
    except:
        log('Command error: %s' % command)
        log(traceback.format_exc())


JOBS = []


def process_jobs():
    while True:
        myjobs = list(JOBS)
        random.shuffle(myjobs)
        for j in myjobs:
            if j.finished:
                log('Completed job: %s' % j)
                JOBS.remove(j)
            else:
                j.run()
        sleep(UPDATE_INTERVAL)


def input_job_from_command_line():
    logger.print_unprinted()
    try:
        command = input('COMMAND: ')
    except EOFError:
        _exit(0)
    if not command:
        return
    job = command_to_job(command)
    return job


def get_random_job():
    commands = config['Misc']['random_commands']
    if commands == '*':
        commands = list(config['Commands'].keys())
    else:
        commands = [c.strip() for c in commands.split(',')]
    command = random.choice(commands)
    job = command_to_job(command)
    return job


def acquire_jobs():
    mode = config['Misc']['mode']
    if mode == 'burroughs':
        burroughs_client = BurroughsClient()
    else:
        burroughs_client = None

    while True:
        sleep(UPDATE_INTERVAL)
        job = None
        if mode == 'manual':
            job = input_job_from_command_line()
        elif mode == 'random':
            job = get_random_job()
        elif mode == 'burroughs':
            job = burroughs_client.poll()
        else:
            raise Exception('Unknown mode.')

        if job is not None:
            log('Adding job: %s' % job)
            JOBS.append(job)
            log('Jobs: %s' % ','.join([j.name for j in JOBS]))
            if mode == 'random':
                while len(JOBS) > int(config['Misc']['random_max_queue']):
                    JOBS.pop(0)
                sleep(int(config['Misc']['random_interval']))


def register_handlers(imported_globals):
    for key in imported_globals:
        if key.startswith('handler_'):
            HANDLERS[key] = imported_globals[key]


def load_objects(imported_globals):
    objs = [g for g in imported_globals.values()
            if isinstance(g, type) and issubclass(g, TableObject)
            and g not in [TableObject]]

    for obj in objs:
        obj.load_all()


def initialize_ramtools(imported_globals):
    register_handlers(imported_globals)
    load_objects(imported_globals)
    client.connect_emulator()


def begin_job_management():
    acquire_thread, process_thread = None, None
    acquire_thread = Thread(target=acquire_jobs, daemon=True)
    acquire_thread.start()
    process_thread = Thread(target=process_jobs, daemon=True)
    process_thread.start()

    log('Beginning main loop.')
    counter = 0
    while True:
        sleep(UPDATE_INTERVAL)
        if counter % 10 == 3:
            acquire_thread.join()
        if counter % 10 == 7:
            process_thread.join()
        try:
            if not acquire_thread.is_alive():
                acquire_thread = Thread(target=acquire_jobs, daemon=True)
                acquire_thread.start()
            if not process_thread.is_alive():
                client.connect_emulator()
                process_thread = Thread(target=process_jobs, daemon=True)
                process_thread.start()
        except(KeyboardInterrupt):
            _exit(0)
        except:
            log(traceback.format_exc())
            client.connect_emulator()
            if not process_thread.is_alive():
                process_thread = Thread(target=process_jobs, daemon=True)
                process_thread.start()
        counter += 1
