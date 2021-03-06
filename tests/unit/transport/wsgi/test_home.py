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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import json

import falcon

import base  # noqa


class TestHomeDocument(base.TestBase):

    config_filename = 'wsgi_sqlite.conf'

    def test_json_response(self):
        body = self.simulate_get('/v1')
        self.assertEquals(self.srmock.status, falcon.HTTP_200)

        content_type = self.srmock.headers_dict['Content-Type']
        self.assertEquals(content_type, 'application/json-home')

        try:
            json.loads(body[0])
        except ValueError:
            self.fail('Home document is not valid JSON')
