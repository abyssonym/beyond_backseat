import random
import traceback
from ramtools import classproperty, client, config, LivePatch, TableObject
from time import sleep, time

def log(msg):
    print(msg)

class CurrentHPObject(TableObject): pass
class CurrentMPObject(TableObject): pass
class MaxHPObject(TableObject): pass
class MaxMPObject(TableObject): pass
class Ailment1SetObject(TableObject): pass
class Ailment2SetObject(TableObject): pass
class Ailment1ActiveObject(TableObject): pass
class Ailment2ActiveObject(TableObject): pass

class PlayerCharacter():
    _every = []

    def __init__(self):
        self.index = len(self._every)
        self._every.append(self)

    @classproperty
    def every(cls):
        return list(cls._every)

    @property
    def offset_index(self):
        return self.index

    @classmethod
    def get(cls, index):
        for o in cls.every:
            if o.index == index:
                return o

    @property
    def hp_objects(self):
        return (CurrentHPObject.get(self.offset_index),
                MaxHPObject.get(self.offset_index))

    @property
    def mp_objects(self):
        return (CurrentMPObject.get(self.offset_index),
                MaxMPObject.get(self.offset_index))

    @property
    def ailment_objects(self):
        return (Ailment1ActiveObject.get(self.offset_index),
                Ailment2ActiveObject.get(self.offset_index))

    @property
    def hp(self):
        for hpo in self.hp_objects:
            hpo.read_data()
        return tuple(hpo.hp for hpo in self.hp_objects)

    @property
    def mp(self):
        for mpo in self.mp_objects:
            mpo.read_data()
        return tuple(mpo.mp for mpo in self.mp_objects)

    @property
    def is_valid_target(self):
        current_hp, max_hp = self.hp
        return 1 <= current_hp <= max_hp

    def set_hp(self, hp):
        current_hp, max_hp = self.hp
        hp = max(0, min(hp, max_hp))
        o = CurrentHPObject.get(self.offset_index)
        o.hp = hp
        o.write_data()

    def set_mp(self, mp):
        current_mp, max_mp = self.mp
        mp = max(0, min(mp, max_mp))
        o = CurrentMPObject.get(self.offset_index)
        o.mp = mp
        o.write_data()

    def get_ailment(self, name):
        for ao in self.ailment_objects:
            if name in ao.bitnames:
                ao.read_data()
                return ao.get_bit(name)

    def set_ailment(self, name, value):
        for to in [Ailment1SetObject, Ailment2SetObject]:
            if name in to.bitnames:
                ao = to.get(self.offset_index)
                ao.read_data()
                ao.set_bit(name, value)
                ao.write_data()
                return
        raise Exception('Unknown ailment: %s' % name)

    def refresh(self):
        for o in self.hp_objects + self.mp_objects + self.ailment_objects:
            o.read_data()


class MonsterCharacter(PlayerCharacter):
    _every = []

    @property
    def offset_index(self):
        return self.index + len(PlayerCharacter.every)


def handler_ailment(ailment_name, target='ally', focus='random'):
    if target == 'ally':
        candidates = PlayerCharacter.every
    elif target == 'enemy':
        candidates = MonsterCharacter.every
    else:
        candidates = PlayerCharacter.every + MonsterCharacter.every

    candidates = [c for c in candidates if c.is_valid_target]
    if not candidates:
        return False

    if focus == 'random':
        chosen = random.choice(candidates)
        chosen.set_ailment(ailment_name, True)
    elif focus == 'all':
        for c in candidates:
            c.set_ailment(ailment_name, True)
    else:
        return False

    return True


def handler_fallenone():
    for pc in PlayerCharacter.every:
        if pc.is_valid_target:
            pc.set_hp(1)

    return True


def dispatch(handler_name, *args, **kwargs):
    handler = globals()['handler_%s' % handler_name]
    return handler(*args, **kwargs)


def run():
    commands = sorted(config['Misc']['surprise'].split(','))
    chosen = config['Commands'][random.choice(commands)]
    if ':' in chosen:
        handler_name, args = chosen.split(':')
        args = args.split(',')
        dispatch(handler_name, *args)
    else:
        dispatch(chosen)


class LiveEvent(LivePatch):
    LOCK_ADDRESS = 0x7e11e8
    IO_WAIT = 0.02
    POLL_INTERVAL = 0.1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous_poll = 0
        self.state = {}
        for key in ['READY', 'EVENT', 'WAIT']:
            setattr(self, key,
                    int(self.definitions[key.lower()], 0x10))
            self.state[key.lower()] = False

    def get_lock_status(self):
        sleep(self.IO_WAIT)
        return client.read_emulator(self.LOCK_ADDRESS, 1)[0]

    def set_lock_bit(self, bit):
        lock = self.get_lock_status()
        if not lock & bit:
            client.send_emulator(self.LOCK_ADDRESS, [lock | bit])

    def unset_lock_bit(self, bit):
        lock = self.get_lock_status()
        if lock & bit:
            client.send_emulator(self.LOCK_ADDRESS, [lock ^ bit])

    def poll_wait(self):
        now = time()
        delta = now - self.previous_poll
        if delta < self.POLL_INTERVAL:
            sleep(self.POLL_INTERVAL - delta)
        self.previous_poll = now

    @property
    def finished(self):
        return self.state['wait']

    def poll(self):
        self.poll_wait()
        if self.finished:
            return

        lock = self.get_lock_status()
        if not (self.state['event']
                or lock & (self.EVENT|self.READY|self.WAIT)):
            self.set_lock_bit(self.EVENT)
            self.state['event'] = True

        if self.state['event']:
            if lock & self.READY and not self.state['ready']:
                self.apply_patch()
                self.unset_lock_bit(self.READY)
                self.state['ready'] = True
            elif lock & self.WAIT:
                self.restore_backup()
                self.unset_lock_bit(self.WAIT)
                self.state['wait'] = True


if __name__ == '__main__':
    try:
        ALL_OBJECTS = [g for g in globals().values()
                   if isinstance(g, type) and issubclass(g, TableObject)
                   and g not in [TableObject]]

        UPDATE_INTERVAL = int(config['Misc']['update_interval'])

        client.connect_emulator()
        lock = client.read_emulator(LiveEvent.LOCK_ADDRESS, 1)[0]
        client.send_emulator(LiveEvent.LOCK_ADDRESS, [0])
        if lock:
            sleep(1)

        LivePatch('cleanup_opcode.patch', force_valid=True).apply_patch()
        LivePatch('inject_event.patch', force_valid=True).apply_patch()
        le = LiveEvent('event_initialization.patch', force_valid=True)
        le2 = LiveEvent('event_rename.patch')
        le2.set_label('character_index', 0)
        while True:
            le.poll()
            le2.poll()

        for obj in ALL_OBJECTS:
            obj.load_all()

        for i in range(4):
            PlayerCharacter()

        for i in range(6):
            MonsterCharacter()

        while True:
            try:
                run()
            except (ConnectionRefusedError, IOError):
                log(traceback.format_exc())
                client.connect_emulator()
            sleep(UPDATE_INTERVAL)
    except:
        log(traceback.format_exc())
        input('Press enter to close this window. ')
        exit(0)
