# Copyright (c) 2013 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time

from testtools import matchers

from marconi.openstack.common import timeutils
from marconi.queues import storage
from marconi.queues.storage import exceptions
from marconi import tests as testing


class ControllerBaseTest(testing.TestBase):
    project = 'project'
    driver_class = None
    controller_class = None
    controller_base_class = None

    def setUp(self):
        super(ControllerBaseTest, self).setUp()

        if not self.driver_class:
            self.skipTest('No driver class specified')

        if not issubclass(self.controller_class, self.controller_base_class):
            self.skipTest('{0} is not an instance of {1}. '
                          'Tests not supported'.format(
                          self.controller_class, self.controller_base_class))

        self.driver = self.driver_class()
        self.controller = self.controller_class(self.driver)


class QueueControllerTest(ControllerBaseTest):
    """Queue Controller base tests."""
    controller_base_class = storage.QueueBase

    def setUp(self):
        super(QueueControllerTest, self).setUp()
        self.message_controller = self.driver.message_controller
        self.claim_controller = self.driver.claim_controller

    def tearDown(self):
        timeutils.clear_time_override()
        super(QueueControllerTest, self).tearDown()

    def test_list(self):
        num = 15
        for queue in xrange(num):
            self.controller.create(queue, project=self.project)

        interaction = self.controller.list(project=self.project,
                                           detailed=True)
        queues = list(next(interaction))

        self.assertEquals(all(map(lambda queue:
                                  'name' in queue and
                                  'metadata' in queue, queues)), True)
        self.assertEquals(len(queues), 10)

        interaction = self.controller.list(project=self.project,
                                           marker=next(interaction))
        queues = list(next(interaction))

        self.assertEquals(all(map(lambda queue:
                                  'name' in queue and
                                  'metadata' not in queue, queues)), True)
        self.assertEquals(len(queues), 5)

    def test_queue_lifecycle(self):
        # Test Queue Creation
        created = self.controller.create('test', project=self.project)
        self.assertTrue(created)

        # Test Queue Existence
        self.assertTrue(self.controller.exists('test', project=self.project))

        # Test Queue retrieval
        metadata = self.controller.get_metadata('test', project=self.project)
        self.assertEqual(metadata, {})

        # Test Queue Update
        created = self.controller.set_metadata('test', project=self.project,
                                               metadata=dict(meta='test_meta'))

        metadata = self.controller.get_metadata('test', project=self.project)
        self.assertEqual(metadata['meta'], 'test_meta')

        # Touching an existing queue does not affect metadata
        created = self.controller.create('test', project=self.project)
        self.assertFalse(created)

        metadata = self.controller.get_metadata('test', project=self.project)
        self.assertEqual(metadata['meta'], 'test_meta')

        # Test Queue Statistic
        _insert_fixtures(self.message_controller, 'test',
                         project=self.project, client_uuid='my_uuid',
                         num=6)

        # NOTE(kgriffs): We can't get around doing this, because
        # we don't know how the storage drive may be calculating
        # message timestamps (and may not be monkey-patchable).
        time.sleep(1)

        _insert_fixtures(self.message_controller, 'test',
                         project=self.project, client_uuid='my_uuid',
                         num=6)

        stats = self.controller.stats('test', project=self.project)
        message_stats = stats['messages']

        self.assertEqual(message_stats['free'], 12)
        self.assertEqual(message_stats['claimed'], 0)
        self.assertEqual(message_stats['total'], 12)

        oldest = message_stats['oldest']
        newest = message_stats['newest']

        self.assertNotEqual(oldest, newest)

        # NOTE(kgriffs): Ensure "now" is different enough
        # for the next comparison to work.
        timeutils.set_time_override()
        timeutils.advance_time_seconds(60)

        for message_stat in (oldest, newest):
            created_iso = message_stat['created']
            created = timeutils.parse_isotime(created_iso)
            self.assertThat(timeutils.normalize_time(created),
                            matchers.LessThan(timeutils.utcnow()))

            self.assertIn('id', message_stat)

        timeutils.clear_time_override()

        self.assertThat(oldest['created'],
                        matchers.LessThan(newest['created']))

        # Test Queue Deletion
        self.controller.delete('test', project=self.project)

        # Test Queue Existence
        self.assertFalse(self.controller.exists('test', project=self.project))

        # Test DoesNotExist Exception
        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.get_metadata('test', project=self.project)

        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.set_metadata('test', '{}', project=self.project)

    def test_stats_for_empty_queue(self):
        created = self.controller.create('test', project=self.project)
        self.assertTrue(created)

        stats = self.controller.stats('test', project=self.project)
        message_stats = stats['messages']

        self.assertEqual(message_stats['free'], 0)
        self.assertEqual(message_stats['claimed'], 0)
        self.assertEqual(message_stats['total'], 0)

        self.assertNotIn('newest', message_stats)
        self.assertNotIn('oldest', message_stats)


class MessageControllerTest(ControllerBaseTest):
    """Message Controller base tests.

    NOTE(flaper87): Implementations of this class should
    override the tearDown method in order
    to clean up storage's state.
    """
    queue_name = 'test_queue'
    controller_base_class = storage.MessageBase

    def setUp(self):
        super(MessageControllerTest, self).setUp()

        # Lets create a queue
        self.queue_controller = self.driver.queue_controller
        self.claim_controller = self.driver.claim_controller
        self.queue_controller.create(self.queue_name, project=self.project)

    def tearDown(self):
        self.queue_controller.delete(self.queue_name, project=self.project)
        super(MessageControllerTest, self).tearDown()

    def test_message_lifecycle(self):
        queue_name = self.queue_name

        messages = [
            {
                'ttl': 60,
                'body': {
                    'event': 'BackupStarted',
                    'backupId': 'c378813c-3f0b-11e2-ad92-7823d2b0f3ce'
                }
            },
        ]

        # Test Message Creation
        created = list(self.controller.post(queue_name, messages,
                                            project=self.project,
                                            client_uuid='unused'))
        self.assertEqual(len(created), 1)

        # Test Message Get
        self.controller.get(queue_name, created[0], project=self.project)

        # Test Message Deletion
        self.controller.delete(queue_name, created[0], project=self.project)

        # Test does not exist
        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.get(queue_name, created[0], project=self.project)

    def test_get_multi(self):
        _insert_fixtures(self.controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid', num=15)

        def load_messages(expected, *args, **kwargs):
            interaction = self.controller.list(*args, **kwargs)
            msgs = list(next(interaction))
            self.assertEqual(len(msgs), expected)
            return interaction

        # Test all messages, echo False and uuid
        load_messages(0, self.queue_name, project=self.project,
                      client_uuid='my_uuid')

        # Test all messages and limit
        load_messages(15, self.queue_name, project=self.project, limit=20,
                      echo=True)

        # Test all messages, echo True, and uuid
        interaction = load_messages(10, self.queue_name, echo=True,
                                    project=self.project,
                                    client_uuid='my_uuid')

        # Test all messages, echo True, uuid and marker
        load_messages(5, self.queue_name, echo=True, project=self.project,
                      marker=next(interaction), client_uuid='my_uuid')

    def test_multi_ids(self):
        messages_in = [{'ttl': 120, 'body': 0}, {'ttl': 240, 'body': 1}]
        ids = self.controller.post(self.queue_name, messages_in,
                                   project=self.project,
                                   client_uuid='my_uuid')

        messages_out = self.controller.bulk_get(self.queue_name, ids,
                                                project=self.project)

        for idx, message in enumerate(messages_out):
            self.assertEquals(message['body'], idx)

        self.controller.bulk_delete(self.queue_name, ids,
                                    project=self.project)

        with testing.expect(StopIteration):
            result = self.controller.bulk_get(self.queue_name, ids,
                                              project=self.project)
            next(result)

    def test_claim_effects(self):
        _insert_fixtures(self.controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid', num=12)

        def list_messages(include_claimed=None):
            kwargs = {
                'project': self.project,
                'client_uuid': 'my_uuid',
                'echo': True,
            }

            # Properly test default value
            if include_claimed is not None:
                kwargs['include_claimed'] = include_claimed

            interaction = self.controller.list(self.queue_name, **kwargs)

            messages = next(interaction)
            return [msg['id'] for msg in messages]

        messages_before = list_messages(True)

        meta = {'ttl': 70, 'grace': 60}
        another_cid, _ = self.claim_controller.create(self.queue_name, meta,
                                                      project=self.project)

        messages_after = list_messages(True)
        self.assertEqual(messages_before, messages_after)

        messages_excluding_claimed = list_messages()
        self.assertNotEqual(messages_before, messages_excluding_claimed)
        self.assertEqual(2, len(messages_excluding_claimed))

        cid, msgs = self.claim_controller.create(self.queue_name, meta,
                                                 project=self.project)
        [msg1, msg2] = msgs

        # A wrong claim does not ensure the message deletion
        with testing.expect(storage.exceptions.NotPermitted):
            self.controller.delete(self.queue_name, msg1['id'],
                                   project=self.project,
                                   claim=another_cid)

        # Make sure a message can be deleted with a claim
        self.controller.delete(self.queue_name, msg1['id'],
                               project=self.project,
                               claim=cid)

        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.get(self.queue_name, msg1['id'],
                                project=self.project)

        # Make sure such a deletion is idempotent
        self.controller.delete(self.queue_name, msg1['id'],
                               project=self.project,
                               claim=cid)

        # A non-existing claim does not ensure the message deletion
        self.claim_controller.delete(self.queue_name, cid,
                                     project=self.project)

        with testing.expect(storage.exceptions.NotPermitted):
            self.controller.delete(self.queue_name, msg2['id'],
                                   project=self.project,
                                   claim=cid)

    def test_expired_message(self):
        messages = [{'body': 3.14, 'ttl': 0}]

        [msgid] = self.controller.post(self.queue_name, messages,
                                       project=self.project,
                                       client_uuid='my_uuid')

        [msgid] = self.controller.post(self.queue_name, messages,
                                       project=self.project,
                                       client_uuid='my_uuid')

        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.get(self.queue_name, msgid,
                                project=self.project)

        countof = self.queue_controller.stats(self.queue_name,
                                              project=self.project)

        self.assertEquals(countof['messages']['free'], 0)

    def test_bad_id(self):
        # NOTE(cpp-cabrera): A malformed ID should result in an empty
        # query. Raising an exception for validating IDs makes the
        # implementation more verbose instead of taking advantage of
        # the Maybe/Optional protocol, particularly when dealing with
        # bulk operations.
        bad_message_id = 'xyz'
        self.controller.delete(self.queue_name,
                               bad_message_id,
                               project=self.project)

        with testing.expect(exceptions.MessageDoesNotExist):
            self.controller.get(self.queue_name,
                                bad_message_id,
                                project=self.project)

    def test_bad_claim_id(self):
        [msgid] = self.controller.post(self.queue_name,
                                       [{'body': {}, 'ttl': 10}],
                                       project=self.project,
                                       client_uuid='my_uuid')

        bad_claim_id = '; DROP TABLE queues'
        self.controller.delete(self.queue_name,
                               msgid,
                               project=self.project,
                               claim=bad_claim_id)

    def test_bad_marker(self):
        bad_marker = 'xyz'
        interaction = self.controller.list(self.queue_name,
                                           project=self.project,
                                           marker=bad_marker)
        messages = list(next(interaction))

        self.assertEquals(messages, [])


class ClaimControllerTest(ControllerBaseTest):
    """Claim Controller base tests.

    NOTE(flaper87): Implementations of this class should
    override the tearDown method in order
    to clean up storage's state.
    """
    queue_name = 'test_queue'
    controller_base_class = storage.ClaimBase

    def setUp(self):
        super(ClaimControllerTest, self).setUp()

        # Lets create a queue
        self.queue_controller = self.driver.queue_controller
        self.message_controller = self.driver.message_controller
        self.queue_controller.create(self.queue_name, project=self.project)

    def tearDown(self):
        self.queue_controller.delete(self.queue_name, project=self.project)
        super(ClaimControllerTest, self).tearDown()

    def test_claim_lifecycle(self):
        _insert_fixtures(self.message_controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid', num=20)

        meta = {'ttl': 70, 'grace': 30}

        # Make sure create works
        claim_id, messages = self.controller.create(self.queue_name, meta,
                                                    project=self.project,
                                                    limit=15)

        messages = list(messages)
        self.assertEquals(len(messages), 15)

        # Ensure Queue stats
        countof = self.queue_controller.stats(self.queue_name,
                                              project=self.project)
        self.assertEqual(countof['messages']['claimed'], 15)
        self.assertEqual(countof['messages']['free'], 5)
        self.assertEqual(countof['messages']['total'], 20)

        # Make sure get works
        claim, messages2 = self.controller.get(self.queue_name, claim_id,
                                               project=self.project)

        messages2 = list(messages2)
        self.assertEquals(len(messages2), 15)
        self.assertEquals(messages, messages2)
        self.assertEquals(claim['ttl'], 70)
        self.assertEquals(claim['id'], claim_id)

        new_meta = {'ttl': 100, 'grace': 60}
        self.controller.update(self.queue_name, claim_id,
                               new_meta, project=self.project)

        # Make sure update works
        claim, messages2 = self.controller.get(self.queue_name, claim_id,
                                               project=self.project)

        messages2 = list(messages2)
        self.assertEquals(len(messages2), 15)

        # TODO(zyuan): Add some tests to ensure the ttl is
        # extended/not-extended.
        for msg1, msg2 in zip(messages, messages2):
            self.assertEquals(msg1['body'], msg2['body'])

        self.assertEquals(claim['ttl'], 100)
        self.assertEquals(claim['id'], claim_id)

        # Make sure delete works
        self.controller.delete(self.queue_name, claim_id,
                               project=self.project)

        self.assertRaises(storage.exceptions.ClaimDoesNotExist,
                          self.controller.get, self.queue_name,
                          claim_id, project=self.project)

    def test_extend_lifetime(self):
        _insert_fixtures(self.message_controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid',
                         num=20, ttl=120)

        meta = {'ttl': 777, 'grace': 0}

        claim_id, messages = self.controller.create(self.queue_name, meta,
                                                    project=self.project)

        for message in messages:
            self.assertEquals(message['ttl'], 777)

    def test_extend_lifetime_with_grace_1(self):
        _insert_fixtures(self.message_controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid',
                         num=20, ttl=120)

        meta = {'ttl': 777, 'grace': 23}

        claim_id, messages = self.controller.create(self.queue_name, meta,
                                                    project=self.project)

        for message in messages:
            self.assertEquals(message['ttl'], 800)

    def test_extend_lifetime_with_grace_2(self):
        _insert_fixtures(self.message_controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid',
                         num=20, ttl=120)

        # Although ttl is less than the message's TTL, the grace
        # period puts it just over the edge.
        meta = {'ttl': 100, 'grace': 22}

        claim_id, messages = self.controller.create(self.queue_name, meta,
                                                    project=self.project)

        for message in messages:
            self.assertEquals(message['ttl'], 122)

    def test_do_not_extend_lifetime(self):
        _insert_fixtures(self.message_controller, self.queue_name,
                         project=self.project, client_uuid='my_uuid',
                         num=20, ttl=120)

        # Choose a ttl that is less than the message's current TTL
        meta = {'ttl': 60, 'grace': 30}

        claim_id, messages = self.controller.create(self.queue_name, meta,
                                                    project=self.project)

        for message in messages:
            self.assertEquals(message['ttl'], 120)

    def test_expired_claim(self):
        meta = {'ttl': 0, 'grace': 60}

        claim_id, messages = self.controller.create(self.queue_name, meta,
                                                    project=self.project)

        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.get(self.queue_name, claim_id,
                                project=self.project)

        with testing.expect(storage.exceptions.DoesNotExist):
            self.controller.update(self.queue_name, claim_id,
                                   meta, project=self.project)

    def test_illformed_id(self):
        # any ill-formed IDs should be regarded as non-existing ones.

        self.controller.delete(self.queue_name,
                               'illformed',
                               project=self.project)

        with testing.expect(exceptions.DoesNotExist):
            self.controller.get(self.queue_name,
                                'illformed',
                                project=self.project)

        with testing.expect(exceptions.DoesNotExist):
            self.controller.update(self.queue_name,
                                   'illformed',
                                   {'ttl': 40},
                                   project=self.project)


def _insert_fixtures(controller, queue_name, project=None,
                     client_uuid=None, num=4, ttl=120):

    def messages():
        for n in xrange(num):
            yield {
                'ttl': ttl,
                'body': {
                    'event': 'Event number {0}'.format(n)
                }}

    controller.post(queue_name, messages(),
                    project=project, client_uuid=client_uuid)
