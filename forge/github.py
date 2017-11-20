# Copyright 2017 datawire. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import fnmatch, os, re, requests
from .tasks import sh, get, project, Elidable, Secret, TaskError

def next_page(response):
    if "Link" in response.headers:
        links = requests.utils.parse_header_links(response.headers["Link"])
        for link in links:
            if link['rel'] == 'next':
                return link['url']
    return None

def inject_token(url, token):
    if not token: return url
    parts = url.split("://", 1)
    if len(parts) == 2:
        return Elidable("%s://" % parts[0], Secret(token), "@%s" % parts[1])
    else:
        return Elidable(Secret(token), "@%s" % parts[0])

class Github(object):

    def __init__(self, token):
        self.token = token
        self._headers = {'Authorization': 'token %s' % self.token} if self.token else None

    def get(self, api):
        return get("https://api.github.com/%s" % api, headers=self._headers)

    def paginate(self, api):
        response = self.get(api)
        yield response
        if response.ok:
            next_url = next_page(response)
            while next_url:
                response = get(next_url, headers=self._headers)
                next_url = next_page(response)
                yield response

    def pull(self, url, directory):
        if not os.path.exists(directory):
            os.makedirs(directory)
            sh("git", "init", cwd=directory)
        sh("git", "pull", inject_token(url, self.token), cwd=directory)

    def list(self, organization, filter="*"):
        repos = []
        for response in self.paginate("orgs/%s/repos" % organization):
            repos.extend(response.json())
        filtered = [r for r in repos if fnmatch.fnmatch(r["full_name"], filter)]

        real_repos = project(lambda x: self.get(x).json(), ["repos/%s" % r["full_name"] for r in filtered])
        urls = [(r["full_name"], r["clone_url"]) for r in real_repos if "id" in r]
        return urls

    def exists(self, url):
        result = sh("git", "-c", "core.askpass=true", "ls-remote", inject_token(url, self.token), "HEAD",
                    expected=xrange(256))
        if result.code == 0:
            return True
        elif re.search(r"(fatal: repository '.*' not found|ERROR: Repository not found)", result.output):
            return False
        else:
            raise TaskError(result)

    def remote(self, directory):
        result = sh("git", "remote", "get-url", "origin", cwd=directory, expected=xrange(256))
        if result.code == 0:
            return result.output.strip()
        else:
            if "Not a git repository" in result.output:
                return None
            else:
                raise TaskError(str(result))

    def clone(self, url, directory):
        sh("git", "-c", "core.askpass=true", "clone", inject_token(url, self.token), directory)
