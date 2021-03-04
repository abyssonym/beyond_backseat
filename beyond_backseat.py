import random
import traceback
from ramtools import classproperty, client, config, TableObject
from time import sleep

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


if __name__ == '__main__':
    try:
        ALL_OBJECTS = [g for g in globals().values()
                   if isinstance(g, type) and issubclass(g, TableObject)
                   and g not in [TableObject]]

        UPDATE_INTERVAL = int(config['Misc']['update_interval'])

        client.connect_emulator()

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
