import random
import threading
import traceback
from os import _exit
from time import sleep, time

from ramtools import (classproperty, client, config, logger, log,
                      LivePatch, TableObject)


logger.set_logfile('beyond_backseat.log')
log('Beginning log.')
UPDATE_INTERVAL = int(config['Misc']['update_interval'])


class CurrentHPObject(TableObject): pass
class CurrentMPObject(TableObject): pass
class MaxHPObject(TableObject): pass
class MaxMPObject(TableObject): pass
class Ailment1SetObject(TableObject): pass
class Ailment2SetObject(TableObject): pass
class Ailment1ActiveObject(TableObject): pass
class Ailment2ActiveObject(TableObject): pass


class PlayerCharacter():
    IO_WAIT = 0.02
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
        for hpo in self.hp_objects:
            sleep(self.IO_WAIT)
            hpo.read_data()
        return tuple(hpo.hp for hpo in self.hp_objects)

    @property
    def mp(self):
        for mpo in self.mp_objects:
            sleep(self.IO_WAIT)
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
                sleep(self.IO_WAIT)
                ao.read_data()
                return ao.get_bit(name)

    def set_ailment(self, name, value):
        for to in [Ailment1SetObject, Ailment2SetObject]:
            if name in to.bitnames:
                sleep(self.IO_WAIT)
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous_poll = 0
        self.state = {}
        for key in ['EVENT', 'READY', 'VERIFY', 'WAIT']:
            if key.lower() in self.definitions:
                setattr(self, key,
                        int(self.definitions[key.lower()], 0x10))
            self.state[key.lower()] = False

    def __repr__(self):
        if hasattr(self, 'name'):
            s = self.name
        else:
            s = self.__class__.name
        s += '-{0:x}'.format(id(self))
        return s

    def get_lock_status(self):
        sleep(self.IO_WAIT)
        self.lock = client.read_emulator(self.LOCK_ADDRESS, 1)[0]
        return self.lock

    def set_lock_bit(self, bit):
        lock = self.get_lock_status()
        if lock & bit != bit:
            client.send_emulator(self.LOCK_ADDRESS, [lock | bit])
            self.lock |= bit

    def unset_lock_bit(self, bit):
        lock = self.get_lock_status()
        if lock & bit:
            client.send_emulator(self.LOCK_ADDRESS, [(lock | bit) ^ bit])
            self.lock = (self.lock | bit) ^ bit

    def reset(self):
        bits = 0
        for key in ['EVENT', 'READY', 'VERIFY', 'WAIT']:
            if hasattr(self, key):
                bits |= getattr(self, key)
            self.state[key.lower()] = False
        self.unset_lock_bit(bits)

    def poll_wait(self):
        now = time()
        delta = now - self.previous_poll
        if delta < self.POLL_INTERVAL:
            sleep(self.POLL_INTERVAL - delta)
        self.previous_poll = now

    def poll(self):
        self.poll_wait()
        if self.finished:
            return

        lock = self.get_lock_status()

        if not (self.state['event'] or lock & self.EVENT):
            self.do_event()

        if self.state['event']:
            for key in ['READY', 'VERIFY', 'WAIT']:
                if (hasattr(self, key) and lock & getattr(self, key)
                        and not self.state[key.lower()]):
                    f = getattr(self, 'do_%s' % key.lower())
                    f()

        self.do_extra()

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

    def run(self):
        self.poll()


class LiveEvent(LiveMixin):
    @property
    def finished(self):
        return self.state['wait']

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
    current_airstrike = None
    every = []

    command_names = [
        'fight', 'item', 'magic', 'morph', 'revert', 'steal', 'capture',
        'swdtech', 'throw', 'tools', 'blitz', 'runic', 'lore', 'sketch',
        'control', 'slot', 'rage', 'leap', 'mimic', 'dance', 'row', 'def',
        'jump', 'xmagic', 'gprain', 'summon', 'health', 'shock', 'possess',
        'magitek',
        ]

    def __init__(self, command=0x02, spell=0x80, target='enemy', focus='all',
                 name=None):
        patch_filename = 'battle_airstrike.patch'
        super().__init__(patch_filename)

        if isinstance(command, str):
            command = self.command_names.index(command)
        self.attack_command = command
        self.attack_spell = spell
        self.target = target
        self.focus = focus
        self.name = name
        LiveAirstrike.every.append(self)

    def __repr__(self):
        s = self.name
        if self.is_current:
            s = '*{0}'.format(s)
        s = '{0}-{1:x}'.format(s, id(self))
        return s

    @property
    def is_current(self):
        return LiveAirstrike.current_airstrike is self

    @property
    def finished(self):
        finished = self.state['wait']
        if finished and self.is_current:
            LiveAirstrike.current_airstrike = None
        return finished

    def reset(self):
        if self.is_current:
            LiveAirstrike.current_airstrike = None
        super().reset()

    def poll(self):
        if LiveAirstrike.current_airstrike is None:
            LiveAirstrike.current_airstrike = self
        if self.is_current:
            super().poll()

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
        elif focus == 'random':
            if target not in ['ally', 'enemy']:
                target = random.choice(['ally', 'enemy'])
            if target == 'ally':
                candidates = [p for p in PlayerCharacter.every
                              if p.is_valid_target]
                actor_candidates = candidates
            elif target == 'enemy':
                candidates = [m for m in MonsterCharacter.every
                              if m.is_valid_target]
            if not candidates:
                self.reset()
                return
            attack_targets = random.choice(candidates).targeting_flag
        else:
            raise Exception('Unknown targeting focus.')

        attack_targets = [attack_targets & 0xff, attack_targets >> 8]
        self.set_label('attack_targets', attack_targets)

        if actor_candidates is None:
            actor_candidates = [p for p in PlayerCharacter.every
                                if p.is_valid_target]
            if not actor_candidates:
                self.reset()
                return

        actor_index = random.choice(actor_candidates).index
        assert 0 <= actor_index <= 3
        caaa = int(self.definitions['counterattack_assignments_address'], 0x10)
        caaa_actor = caaa + (actor_index * 2)
        #assert caaa_actor not in self.patch
        self.patch[caaa_actor] = 0

        tail = client.read_emulator(
            self.labels['counterattacker_queue_tail'], 1)[0]

        caqa = int(self.definitions['counterattacker_queue_address'], 0x10)
        caqa_tail = caqa + tail
        #assert caqa_tail not in self.patch
        self.patch[caqa_tail] = actor_index * 2

        tail = (tail + 1) & 0xff
        self.set_label('counterattacker_queue_tail', tail)
        self.state['ready'] = True
        self.set_lock_bit(self.VERIFY)
        self.unset_lock_bit(self.WAIT)
        self.client.send_emulator(self.VERIFY_COMMAND, [self.attack_command,
                                                        self.attack_spell])
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


def handler_event(patch_filename, name=None):
    return LiveEvent(patch_filename, name=name)


def handler_airstrike(command, spell, target, focus, name=None):
    return LiveAirstrike(command, spell, target, focus, name=name)


def dispatch_to_job(handler_name, *args, **kwargs):
    handler = globals()['handler_%s' % handler_name]
    return handler(*args, **kwargs)


def command_to_job(command):
    try:
        s = config['Commands'][command]
        if ':' in s:
            handler_name, args = s.split(':')
            args = args.split(',')
            args = [int(a[2:], 0x10) if a.startswith('0x') else
                    int(a) if a.isdigit() else a for a in args]
        else:
            handler_name, args = s, []
        log('Running command: %s %s %s' % (command, handler_name, args))
        return dispatch_to_job(handler_name, *args, name=command)
    except:
        log('Command error: %s' % command)
        log(traceback.format_exc())


JOBS = []


def process_jobs():
    while True:
        for j in list(JOBS):
            if j.finished:
                log('Completed job: %s' % j)
                JOBS.remove(j)
            else:
                j.run()
        sleep(UPDATE_INTERVAL)


def input_job_from_command_line():
    command = input('COMMAND: ')
    job = command_to_job(command)
    return job


def get_random_job():
    commands = config['Misc']['random_commands']
    commands = commands.split(',')
    command = random.choice(commands)
    job = command_to_job(command)
    return job


def acquire_jobs():
    mode = config['Misc']['mode']
    while True:
        sleep(UPDATE_INTERVAL)
        job = None
        if mode == 'manual':
            job = input_job_from_command_line()
        elif mode == 'random':
            job = get_random_job()
        elif mode == 'burroughs':
            raise NotImplementedError
        else:
            raise Exception('Unknown mode.')

        if job is not None:
            log('Adding job: %s' % job)
            JOBS.append(job)
            log('Jobs: %s' % ','.join([j.name for j in JOBS]))
            if mode == 'random':
                sleep(int(config['Misc']['random_interval']))


if __name__ == '__main__':
    acquire_thread, process_thread = None, None
    try:
        ALL_OBJECTS = [g for g in globals().values()
                   if isinstance(g, type) and issubclass(g, TableObject)
                   and g not in [TableObject]]

        client.connect_emulator()
        lock = client.read_emulator(LiveEvent.LOCK_ADDRESS, 1)[0]
        client.send_emulator(LiveEvent.LOCK_ADDRESS, [0])
        if lock:
            sleep(1)

        LivePatch('cleanup_opcode.patch').apply_patch()
        LivePatch('inject_event.patch').apply_patch()
        LivePatch('battle_wait.patch').apply_patch()

        SKIP_INITIALIZATION = True
        if not SKIP_INITIALIZATION:
            le = LiveEvent('event_initialization.patch')
            while not le.finished:
                le.run()

        for obj in ALL_OBJECTS:
            obj.load_all()

        for i in range(4):
            PlayerCharacter()

        for i in range(6):
            MonsterCharacter()

        acquire_thread = threading.Thread(target=acquire_jobs)
        acquire_thread.start()
        process_thread = threading.Thread(target=process_jobs)
        process_thread.start()

        log('Beginning main loop.')
        while True:
            try:
                if not acquire_thread.is_alive():
                    acquire_thread = threading.Thread(target=acquire_jobs)
                    acquire_thread.start()
                if not process_thread.is_alive():
                    client.connect_emulator()
                    process_thread = threading.Thread(target=process_jobs)
                    process_thread.start()
            except(KeyboardInterrupt):
                break
            except:
                log(traceback.format_exc())
                client.connect_emulator()
                if not process_thread.is_alive():
                    process_thread = threading.Thread(target=process_jobs)
                    process_thread.start()
            sleep(UPDATE_INTERVAL)
    except:
        log(traceback.format_exc())
        logger.logfile.close()
        try:
            input('Press enter to close this window. ')
        except(KeyboardInterrupt):
            pass
        _exit(0)
