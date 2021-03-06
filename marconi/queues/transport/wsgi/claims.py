# Copyright (c) 2013 Rackspace, Inc.
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

import falcon

from marconi.common import config
from marconi.common import exceptions as input_exceptions
import marconi.openstack.common.log as logging
from marconi.queues.storage import exceptions as storage_exceptions
from marconi.queues.transport import utils
from marconi.queues.transport import validation as validate
from marconi.queues.transport.wsgi import exceptions as wsgi_exceptions
from marconi.queues.transport.wsgi import utils as wsgi_utils


LOG = logging.getLogger(__name__)
CFG = config.namespace('drivers:transport:wsgi').from_options(
    metadata_max_length=64 * 1024
)

CLAIM_POST_SPEC = (('ttl', int), ('grace', int))
CLAIM_PATCH_SPEC = (('ttl', int),)


class CollectionResource(object):

    __slots__ = ('claim_controller')

    def __init__(self, claim_controller):
        self.claim_controller = claim_controller

    def on_post(self, req, resp, project_id, queue_name):
        LOG.debug(_(u'Claims collection POST - queue: %(queue)s, '
                    u'project: %(project)s') %
                  {'queue': queue_name, 'project': project_id})

        # Check for an explicit limit on the # of messages to claim
        limit = req.get_param_as_int('limit')
        claim_options = {} if limit is None else {'limit': limit}

        # Place JSON size restriction before parsing
        if req.content_length > CFG.metadata_max_length:
            description = _(u'Claim metadata size is too large.')
            raise wsgi_exceptions.HTTPBadRequestBody(description)

        # Read claim metadata (e.g., TTL) and raise appropriate
        # HTTP errors as needed.
        metadata, = wsgi_utils.filter_stream(req.stream, req.content_length,
                                             CLAIM_POST_SPEC)

        # Claim some messages
        try:
            validate.claim_creation(metadata, **claim_options)
            cid, msgs = self.claim_controller.create(
                queue_name,
                metadata=metadata,
                project=project_id,
                **claim_options)

            # Buffer claimed messages
            # TODO(kgriffs): optimize, along with serialization (below)
            resp_msgs = list(msgs)

        except input_exceptions.ValidationFailed as ex:
            raise wsgi_exceptions.HTTPBadRequestBody(str(ex))

        except Exception as ex:
            LOG.exception(ex)
            description = _(u'Claim could not be created.')
            raise wsgi_exceptions.HTTPServiceUnavailable(description)

        # Serialize claimed messages, if any. This logic assumes
        # the storage driver returned well-formed messages.
        if len(resp_msgs) != 0:
            for msg in resp_msgs:
                msg['href'] = _msg_uri_from_claim(
                    req.path.rpartition('/')[0], msg['id'], cid)

                del msg['id']

            resp.location = req.path + '/' + cid
            resp.body = utils.to_json(resp_msgs)
            resp.status = falcon.HTTP_201
        else:
            resp.status = falcon.HTTP_204


class ItemResource(object):

    __slots__ = ('claim_controller')

    def __init__(self, claim_controller):
        self.claim_controller = claim_controller

    def on_get(self, req, resp, project_id, queue_name, claim_id):
        LOG.debug(_(u'Claim item GET - claim: %(claim_id)s, '
                    u'queue: %(queue_name)s, project: %(project_id)s') %
                  {'queue_name': queue_name,
                   'project_id': project_id,
                   'claim_id': claim_id})
        try:
            meta, msgs = self.claim_controller.get(
                queue_name,
                claim_id=claim_id,
                project=project_id)

            # Buffer claimed messages
            # TODO(kgriffs): Optimize along with serialization (see below)
            meta['messages'] = list(msgs)

        except storage_exceptions.DoesNotExist:
            raise falcon.HTTPNotFound()
        except Exception as ex:
            LOG.exception(ex)
            description = _(u'Claim could not be queried.')
            raise wsgi_exceptions.HTTPServiceUnavailable(description)

        # Serialize claimed messages
        # TODO(kgriffs): Optimize
        for msg in meta['messages']:
            msg['href'] = _msg_uri_from_claim(
                req.path.rsplit('/', 2)[0], msg['id'], meta['id'])
            del msg['id']

        meta['href'] = req.path
        del meta['id']

        resp.content_location = req.relative_uri
        resp.body = utils.to_json(meta)
        # status defaults to 200

    def on_patch(self, req, resp, project_id, queue_name, claim_id):
        LOG.debug(_(u'Claim Item PATCH - claim: %(claim_id)s, '
                    u'queue: %(queue_name)s, project:%(project_id)s') %
                  {'queue_name': queue_name,
                   'project_id': project_id,
                   'claim_id': claim_id})

        # Place JSON size restriction before parsing
        if req.content_length > CFG.metadata_max_length:
            description = _(u'Claim metadata size is too large.')
            raise wsgi_exceptions.HTTPBadRequestBody(description)

        # Read claim metadata (e.g., TTL) and raise appropriate
        # HTTP errors as needed.
        metadata, = wsgi_utils.filter_stream(req.stream, req.content_length,
                                             CLAIM_PATCH_SPEC)

        try:
            validate.claim_updating(metadata)
            self.claim_controller.update(queue_name,
                                         claim_id=claim_id,
                                         metadata=metadata,
                                         project=project_id)

            resp.status = falcon.HTTP_204

        except input_exceptions.ValidationFailed as ex:
            raise wsgi_exceptions.HTTPBadRequestBody(str(ex))

        except storage_exceptions.DoesNotExist:
            raise falcon.HTTPNotFound()

        except Exception as ex:
            LOG.exception(ex)
            description = _(u'Claim could not be updated.')
            raise wsgi_exceptions.HTTPServiceUnavailable(description)

    def on_delete(self, req, resp, project_id, queue_name, claim_id):
        LOG.debug(_(u'Claim item DELETE - claim: %(claim_id)s, '
                    u'queue: %(queue_name)s, project: %(project_id)s') %
                  {'queue_name': queue_name,
                   'project_id': project_id,
                   'claim_id': claim_id})
        try:
            self.claim_controller.delete(queue_name,
                                         claim_id=claim_id,
                                         project=project_id)

            resp.status = falcon.HTTP_204

        except Exception as ex:
            LOG.exception(ex)
            description = _(u'Claim could not be deleted.')
            raise wsgi_exceptions.HTTPServiceUnavailable(description)


# TODO(kgriffs): Clean up/optimize and move to wsgi.utils
def _msg_uri_from_claim(base_path, msg_id, claim_id):
    return '/'.join(
        [base_path, 'messages', msg_id]
    ) + falcon.to_query_str({'claim_id': claim_id})
