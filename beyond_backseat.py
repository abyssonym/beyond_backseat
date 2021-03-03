import traceback
from ramtools import client, TableObject, classproperty
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

    def set_hp(self, hp):
        current_hp, max_hp = self.hp
        hp = max(0, min(hp, max_hp))
        CurrentHPObject.get(self.offset_index).hp = hp

    def set_mp(self, mp):
        current_mp, max_mp = self.mp
        mp = max(0, min(mp, max_mp))
        CurrentMPObject.get(self.offset_index).mp = mp

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
                return ao.set_bit(name, value)

    def refresh(self):
        for o in self.hp_objects + self.mp_objects + self.ailment_objects:
            o.read_data()


class MonsterCharacter(PlayerCharacter):
    _every = []

    @property
    def offset_index(self):
        return self.index + len(PlayerCharacter.every)


if __name__ == '__main__':
    try:
        ALL_OBJECTS = [g for g in globals().values()
                   if isinstance(g, type) and issubclass(g, TableObject)
                   and g not in [TableObject]]

        client.connect_emulator()

        for obj in ALL_OBJECTS:
            obj.load_all()

        for i in range(4):
            PlayerCharacter()

        for i in range(6):
            MonsterCharacter()

        while True:
            try:
                for m in PlayerCharacter.every:
                    print(m.hp)
                print()
                sleep(3)
            except (ConnectionRefusedError, IOError):
                log(traceback.format_exc())
                sleep(5)
                client.connect_emulator()
    except:
        log(traceback.format_exc())
        input('Press enter to close this window. ')
        exit(0)
