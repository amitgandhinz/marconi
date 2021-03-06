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

from marconi.common import config
from marconi import tests as testing


PROJECT_CONFIG = config.project()
CFG = PROJECT_CONFIG.from_options(
    without_help=3,
    with_help=(None, 'nonsense'))


class TestConfig(testing.TestBase):

    def test_cli(self):
        args = ['--with_help', 'sense']
        PROJECT_CONFIG.load(self.conf_path('wsgi_sqlite.conf'), args)
        self.assertEquals(CFG.with_help, 'sense')

        PROJECT_CONFIG.load(args=[])
        self.assertEquals(CFG.with_help, None)

    def test_wrong_type(self):
        ns = config.namespace('local')
        with testing.expect(config.cfg.Error):
            ns.from_options(opt={})
