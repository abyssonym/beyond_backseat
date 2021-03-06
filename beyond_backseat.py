import random
import traceback
from os import _exit
from time import sleep, time

from ramtools import (classproperty, client, config, logger, log,
                      initialize_ramtools, begin_job_management,
                      LivePatch, TableObject)


VERSION = 3


logger.set_logfile('beyond_backseat.log')
if config['Misc']['mode'] == 'manual':
    logger.print_logs = False
log('Beginning log.')


class PartyDataObject(TableObject): pass
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

    @property
    def targeting_flag(self):
        return 1 << self.index

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
        return tuple(hpo.hp for hpo in self.hp_objects)

    @property
    def mp(self):
        return tuple(mpo.mp for mpo in self.mp_objects)

    @property
    def is_valid_target(self):
        self.refresh()
        current_hp, max_hp = self.hp
        if not (1 <= current_hp <= max_hp):
            return False

        INVALID_AILMENTS = ['zombie', 'petrify', 'death',
                            'sleep', 'stop', 'frozen', 'removed']
        for ailment in INVALID_AILMENTS:
            if self.get_ailment(ailment):
                return False

        return True

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

    @property
    def targeting_flag(self):
        return 1 << (self.index + 8)


class LiveMixin(LivePatch):
    POLL_INTERVAL = 0.1
    LOCK_ADDRESS = 0x7e11e8
    IO_WAIT = 0.02
    MAX_LOCK_WAIT = 10
    CURRENTS = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous_poll = 0
        self.state = {}
        self.last_update = None
        for key in ['EVENT', 'READY', 'VERIFY', 'WAIT']:
            if key.lower() in self.definitions:
                setattr(self, key,
                        int(self.definitions[key.lower()], 0x10))
            self.state[key.lower()] = False

        self.LOCK_KEY = (self.LOCK_ADDRESS, self.EVENT)

        if self.LOCK_KEY not in self.CURRENTS:
            self.CURRENTS[self.LOCK_KEY] = None

    def __repr__(self):
        if hasattr(self, 'name') and self.name:
            s = self.name
        else:
            s = self.__class__.__name__

        status = None
        for key in ['EVENT', 'READY', 'VERIFY', 'WAIT']:
            key = key.lower()
            if key in self.state and self.state[key]:
                status = key

        if status is None:
            s = '{0}-{1:x}'.format(s, id(self) & 0xffff)
        else:
            s = '{0}-{1:x}-{2}'.format(s, id(self) & 0xffff, status)

        if self.is_current:
            s = '*%s' % s

        return s

    @property
    def is_disposable(self):
        if self.is_current:
            return False
        if not hasattr(self, 'state'):
            return False
        if 'event' not in self.state:
            return False
        if self.state['event']:
            return False
        if 'ready' in self.state and self.state['ready']:
            return False
        return True

    @property
    def finished(self):
        return self.state['wait']

    @property
    def is_current(self):
        current = self.CURRENTS[self.LOCK_KEY]
        if current and current.finished:
            self.CURRENTS[self.LOCK_KEY] = None
        if current is self:
            return True
        return False

    def get_lock_status(self):
        sleep(self.IO_WAIT)
        lock = client.read_emulator(self.LOCK_ADDRESS, 1)[0]
        if hasattr(self, 'lock') and lock != self.lock:
            for key in ['EVENT', 'READY', 'VERIFY', 'WAIT']:
                if hasattr(self, key):
                    bit = getattr(self, key)
                    if lock & bit != self.lock & bit:
                        self.last_update = time()

        self.lock = lock
        return self.lock

    def set_lock_bit(self, bit):
        lock = self.get_lock_status()
        if lock & bit != bit:
            self.last_update = time()
            client.send_emulator(self.LOCK_ADDRESS, [lock | bit])
            self.lock |= bit
            sleep(self.IO_WAIT)

    def unset_lock_bit(self, bit):
        lock = self.get_lock_status()
        if lock & bit:
            self.last_update = time()
            client.send_emulator(self.LOCK_ADDRESS, [(lock | bit) ^ bit])
            self.lock = (self.lock | bit) ^ bit
            sleep(self.IO_WAIT)

    def reset(self):
        if self.finished:
            return
        bits = 0
        for key in ['EVENT', 'READY', 'VERIFY', 'WAIT']:
            if hasattr(self, key):
                bits |= getattr(self, key)
            self.state[key.lower()] = False
        self.unset_lock_bit(bits)
        if self.is_current:
            self.CURRENTS[self.LOCK_KEY] = None
        assert not self.is_current
        sleep(self.IO_WAIT)

    def poll_wait(self):
        now = time()
        delta = now - self.previous_poll
        if delta < self.POLL_INTERVAL:
            sleep(self.POLL_INTERVAL - delta)
        self.previous_poll = now

    def poll(self):
        if self.finished:
            if self.is_current:
                self.CURRENTS[self.LOCK_KEY] = None
            return

        if self.CURRENTS[self.LOCK_KEY] is None:
            self.CURRENTS[self.LOCK_KEY] = self
            assert self.is_current

        if not self.is_current:
            return

        self.poll_wait()
        lock = self.get_lock_status()
        self.break_lock()

        old_state = dict(self.state)
        if not (self.state['event'] or lock & self.EVENT):
            self.do_event()

        if self.state['event']:
            for key in ['READY', 'VERIFY', 'WAIT']:
                if (hasattr(self, key) and lock & getattr(self, key)
                        and not self.state[key.lower()]):
                    f = getattr(self, 'do_%s' % key.lower())
                    f()

        self.do_extra()

        if self.state != old_state:
            lock = self.get_lock_status()

        state_progress = False
        lock_progress = False
        state_progress =  any([self.state[k] for k in
                               ['event', 'ready', 'verify', 'wait']])
        lock_progress = any([lock & getattr(self, k)
                            for k in ['EVENT', 'READY', 'VERIFY', 'WAIT']
                            if hasattr(self, k)])
        if state_progress and not lock_progress:
            self.reset()

    def do_event(self):
        self.set_lock_bit(self.EVENT)
        self.state['event'] = True

    def do_ready(self):
        pass

    def do_verify(self):
        pass

    def do_wait(self):
        pass

    def do_extra(self):
        pass

    def break_lock(self):
        if self.finished or self.state['ready'] or not self.is_current:
            return

        now = time()
        if self.last_update is None:
            self.last_update = now

        if (self.last_update and self.lock & self.EVENT
                and now - self.last_update > self.MAX_LOCK_WAIT):
            self.reset()

    def run(self):
        self.poll()


class LiveEvent(LiveMixin):
    def do_ready(self):
        self.apply_patch()
        self.unset_lock_bit(self.READY)
        self.state['ready'] = True

    def do_wait(self):
        self.restore_backup()
        self.unset_lock_bit(self.WAIT)
        self.state['wait'] = True
        assert self.finished


class LiveAirstrike(LiveMixin):
    LOCK_ADDRESS = 0x7e11e8
    VERIFY_COMMAND = 0x7e11ea
    VERIFY_SPELL = VERIFY_COMMAND + 1
    MAX_LOCK_WAIT = 6
    current_airstrike = None
    every = []

    command_names = [
        'fight', 'item', 'magic', 'morph', 'revert', 'steal', 'capture',
        'swdtech', 'throw', 'tools', 'blitz', 'runic', 'lore', 'sketch',
        'control', 'slot', 'rage', 'leap', 'mimic', 'dance', 'row', 'def',
        'jump', 'xmagic', 'gprain', 'summon', 'health', 'shock', 'possess',
        'magitek',
        ]

    def __init__(self, name, command=0x02, spell=0x80,
                 target='enemy', focus='all', caster='ally'):
        patch_filename = 'battle_airstrike.patch'
        super().__init__(name, patch_filename)

        if isinstance(command, str):
            command = self.command_names.index(command)
        self.attack_command = command
        self.attack_spell = spell
        self.target = target
        self.focus = focus
        self.name = name
        self.caster = caster
        LiveAirstrike.every.append(self)
        client.show_message('Airstrike: {0}'.format(name.upper()))

    def reset(self):
        super().reset()
        if self.is_current:
            LiveAirstrike.current_airstrike = None

    def do_ready(self):
        self.set_label('attack_command', self.attack_command)
        self.set_label('attack_spell', self.attack_spell)

        target, focus = self.target, self.focus
        actor_candidates = None
        if focus == 'all':
            if target == 'ally':
                attack_targets = 0x000f
            elif target == 'enemy':
                attack_targets = 0x3f00
            else:
                attack_targets = 0x3f0f
        elif focus in ['random', 'self']:
            if target not in ['ally', 'enemy']:
                target = random.choice(['ally', 'enemy'])

            if target == 'ally':
                candidates = [p for p in PlayerCharacter.every
                              if p.is_valid_target]
            elif target == 'enemy':
                candidates = [m for m in MonsterCharacter.every
                              if m.is_valid_target]

            if self.caster == target:
                actor_candidates = candidates

            if not candidates:
                self.reset()
                return

            chosen_target = random.choice(candidates)
            attack_targets = chosen_target.targeting_flag
            if focus == 'self':
                assert self.caster == target
                actor_candidates = [chosen_target]
        else:
            raise Exception('Unknown targeting focus.')

        if actor_candidates is None:
            if self.caster == 'ally':
                actor_candidates = [p for p in PlayerCharacter.every
                                    if p.is_valid_target]
            elif self.caster == 'enemy':
                actor_candidates = [m for m in MonsterCharacter.every
                                    if m.is_valid_target]
            if not actor_candidates:
                self.reset()
                sleep(self.MAX_LOCK_WAIT)
                return

        actor_index = random.choice(actor_candidates).offset_index
        if self.caster == 'ally':
            assert 0 <= actor_index <= 3
        elif self.caster == 'enemy':
            assert 4 <= actor_index <= 9

        if target == 'ally':
            assert attack_targets & 0x000f
        elif target == 'enemy':
            assert attack_targets & 0x3f00

        attack_targets = [attack_targets & 0xff, attack_targets >> 8]
        self.set_label('attack_targets', attack_targets)

        caaa = int(self.definitions['counterattack_assignments_address'], 0x10)
        caaa_actor = caaa + (actor_index * 2)

        tail = client.read_emulator(
            self.labels['counterattacker_queue_tail'], 1)[0]

        caqa = int(self.definitions['counterattacker_queue_address'], 0x10)
        caqa_tail = caqa + tail

        tail = (tail + 1) & 0xff
        self.set_label('counterattacker_queue_tail', tail)
        self.state['ready'] = True
        self.set_lock_bit(self.VERIFY)
        self.unset_lock_bit(self.WAIT)
        self.client.send_emulator(self.VERIFY_COMMAND, [self.attack_command,
                                                        self.attack_spell])
        self.client.send_emulator(caaa_actor, [0])
        self.client.send_emulator(caqa_tail, [actor_index * 2])
        self.apply_patch()

    def do_wait(self):
        self.state['wait'] = True
        self.unset_lock_bit(self.EVENT|self.READY|self.WAIT|self.VERIFY)
        self.client.send_emulator(self.VERIFY_COMMAND, [0, 0])
        assert self.finished

    def do_extra(self):
        if (self.is_current and self.state['event']
                and not self.finished
                and not self.lock & self.EVENT):
            self.reset()


def handler_event(name, patch_filename):
    return LiveEvent(name, patch_filename)


def handler_airstrike(name, command, spell, target, focus, caster='ally'):
    return LiveAirstrike(name, command, spell, target, focus, caster=caster)


class PartyChangeEvent(LiveEvent):
    MAP_INDEX_ADDRESS = 0x7e1f64
    MAP_X_ADDRESS = 0x7e00af
    MAP_Y_ADDRESS = 0x7e00b0

    def __init__(self, name, patch_filename, locked_character=None):
        self.locked_character = locked_character
        super().__init__(name, patch_filename)

    def check_valid(self):
        for pdo in PartyDataObject.every:
            pdo.read_data()

        if any([pdo.get_bit('p2') or pdo.get_bit('p3')
                for pdo in PartyDataObject.every]):
            return False

        if any([pdo.get_bit('p1') for pdo in PartyDataObject.every]):
            return True

        return False

    def do_event(self):
        if not self.check_valid():
            return
        super().do_event()

    def do_ready(self):
        if self.locked_character is not None:
            remove_characters = [0x3f, self.locked_character, 0x00]
            remove_characters_2 = []
            for pdo in PartyDataObject.every:
                if pdo.index == self.locked_character:
                    continue
                if pdo.get_bit('p1'):
                    remove_characters.extend([0x3f, pdo.index, 0x00])
                    remove_characters.extend([0x3d, pdo.index])
                    remove_characters_2.extend([0x42, pdo.index])
                else:
                    remove_characters.extend([0x3e, pdo.index])
                    pass
            remove_characters.extend([0x3f, self.locked_character, 0x01])
            self.set_label('remove_characters', remove_characters,
                           change_length=True)
            self.set_label('remove_characters_2', remove_characters_2,
                           change_length=True)
        map_index = self.client.read_emulator(self.MAP_INDEX_ADDRESS, 2)
        map_index = [map_index[0], 0x20 | (map_index[1] & 1)]
        self.set_label('map_index', map_index)

        map_x = self.client.read_emulator(self.MAP_X_ADDRESS, 1)
        self.set_label('x_coordinate', map_x)
        map_y = self.client.read_emulator(self.MAP_Y_ADDRESS, 1)
        self.set_label('y_coordinate', map_y)

        if self.name == 'banon':
            self.client.show_message('Good news! BANON is here to help!')

        self.make_backup()
        super().do_ready()


def handler_banon(name):
    return PartyChangeEvent(name, 'event_banon.patch', locked_character=0xe)


def handler_umaro(name):
    return PartyChangeEvent(name, 'event_umaro.patch', locked_character=0xd)


def handler_party_change(name):
    return PartyChangeEvent(name, 'event_party_change.patch')


class AirshipEvent(LiveEvent):
    # These are overworld events, which means they use the lock flag #$10
    # and have a different scripting language.
    MAP_INDEX_ADDRESS = 0x7e1f64
    MAP_X_ADDRESS = 0x7e00e0
    MAP_Y_ADDRESS = 0x7e00e2
    EVENT_BITS_ADDRESS = 0x7e1e80
    LOCK_ADDRESS = 0x7e11e9

    def __init__(self, name, patch_filename, world, vehicle):
        self.world = world.lower()

        if isinstance(vehicle, str):
            self.vehicle = {'airship': 1,
                            'chocobo': 2}[vehicle]
        else:
            self.vehicle = vehicle
        assert isinstance(self.vehicle, int)

        super().__init__(name, patch_filename)

    @property
    def finished(self):
        return self.state['ready']

    def set_event_bit(self, bit_index, truth=True):
        address = self.EVENT_BITS_ADDRESS + (bit_index >> 3)
        bit = 1 << (bit_index & 0b111)
        old_value = self.client.read_emulator(address, 1)[0]
        if truth:
            value = old_value | bit
        else:
            value = (old_value | bit) ^ bit
        if value != old_value:
            self.client.send_emulator(address, [value])

    def do_ready(self):
        if self.world == 'balance':
            self.map_index = 0
            self.set_event_bit(0xa4, False)
        elif self.world == 'ruin':
            self.map_index = 1
            self.set_event_bit(0xa4, True)
        else:
            map_index = self.client.read_emulator(self.MAP_INDEX_ADDRESS, 2)
            map_index = map_index[0] | (map_index[1] << 8)
            if not 0 <= map_index & 0x1ff <= 1:
                self.reset()
                return
            self.map_index = map_index & 1
        map_index = [self.map_index & 0xff, self.map_index >> 8]
        self.set_label('map_index', map_index)
        map_x = self.client.read_emulator(self.MAP_X_ADDRESS, 1)
        self.set_label('x_coordinate', map_x)
        map_y = self.client.read_emulator(self.MAP_Y_ADDRESS, 1)
        self.set_label('y_coordinate', map_y)
        self.set_label('vehicle', self.vehicle)
        if self.vehicle == 1:
            self.set_event_bit(0x16f, True)
            self.set_event_bit(0x1b9, True)
        super().do_ready()
        self.state['wait'] = True


def handler_airship(name, world, vehicle):
    if vehicle == 'chocobo':
        client.show_message('WARK!!')
    elif world == 'ruin':
        client.show_message('On that day, the world was changed forever...')
    elif world == 'balance':
        client.show_message("Delightful is the light of dawn. "
                            "Noble is the heart of man.")
    elif vehicle == 'airship':
        client.show_message("We're gonna get us another one... "
                            "Airship, that is!")
    return AirshipEvent(name, 'event_airship.patch', world, vehicle)


def handler_boss(name, formation_index):
    if name == 'ghosttrain':
        client.show_message('ALL ABOARD!!')
    elif name == 'dadaluma':
        client.show_message('Good day, gentle folks. Can I be of service?')
    elif name == 'doom-gaze':
        client.show_message('A black wind howls...')
    elif name == 'ultros':
        client.show_message("Don't tease the octopus, kids!")
    elif name == 'siegfried':
        client.show_message('Aha! The ox bellows!')

    le = LiveEvent(name, 'event_boss.patch')
    le.set_label('formation_index', formation_index)
    return le


def handler_semaphore(name):
    ADDRESS = 0x7e11e8
    DATA = [0, 0]
    client.send_emulator(ADDRESS, DATA)


def handler_rename(name, character_index):
    le = LiveEvent(name, 'event_rename.patch')
    le.set_label('character_index', character_index)
    return le


def main():
    log('You are running Beyond Backseat version %s.' % VERSION, debug=True)
    initialize_ramtools(globals())
    client.send_emulator(LiveEvent.LOCK_ADDRESS, [0])

    if 'free_space_bank' in config['Emulator']:
        LivePatch.GLOBAL_DEFINITIONS['XX'] = (
            config['Emulator']['free_space_bank'])
    else:
        LivePatch.GLOBAL_DEFINITIONS['XX'] = 'c0'
    LivePatch(None, 'cleanup_opcode.patch').apply_patch()
    LivePatch(None, 'inject_event.patch').apply_patch()
    LivePatch(None, 'battle_wait.patch').apply_patch()
    LivePatch(None, 'inject_overworld.patch').apply_patch()

    for i in range(4):
        PlayerCharacter()

    for i in range(6):
        MonsterCharacter()

    begin_job_management()


if __name__ == '__main__':
    try:
        main()
    except:
        log(traceback.format_exc(), debug=True)
        logger.logfile.close()
        try:
            input('Press enter to close this window. ')
        except(KeyboardInterrupt):
            pass
        _exit(0)
