import socket
from configparser import ConfigParser
from os import path
from sys import argv
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

    config = config._sections['Settings']
    for key in list(config.keys()):
        config[key.upper()] = config[key]

except:
    raise Exception('Configuration file error. ')

class classproperty(property):
    def __get__(self, inst, cls):
        return self.fget(cls)


class ParityClient():
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
        self.emulator_socket.send(cmd.encode())
        expected_length = 21 + (3 * num_bytes)
        try:
            data = self.emulator_socket.recv(expected_length)
        except socket.timeout:
            raise IOError('Emulator not responding.')
        data = data.decode('ascii').strip()
        data = [int(d, 0x10) for d in data.split(' ')[2:]]
        if len(data) != num_bytes:
            raise IOError('Emulator RAM data read error: {0}/{1} bytes'.format(
                len(data), num_bytes))
        return data


client = ParityClient(config['RETROARCH_ADDRESS'], config['RETROARCH_PORT'])


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
            line = line.strip()
            if (not line) or line[0] == '#':
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

        try:
            assert self.packed_data == packed_data
        except:
            print(self.packed_data, packed_data)
            import pdb; pdb.set_trace()

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
