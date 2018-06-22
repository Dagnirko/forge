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

import os, pexpect, sys, time, yaml
from .common import mktree, defuzz
from forge.tasks import sh

START_TIME = time.time()
MANGLE = str(START_TIME).replace('.', '-')

APP = r"""
@@forgetest/Dockerfile
# Run server
FROM alpine:3.5
RUN apk add --no-cache python py2-pip py2-gevent
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . /app
WORKDIR /app
EXPOSE 8080
ENTRYPOINT ["python"]
CMD ["app.py"]
@@

@@forgetest/service.yaml
name: forgetest-MANGLE  # name of the service

# The service 'track' can be used to easily implement the pattern described here:
#
#   https://kubernetes.io/docs/concepts/cluster-administration/manage-deployment/#canary-deployments
#
# The default track is named 'stable'. Each track is concurrently
# deployed in order ot enable multiple long lived canaries:
#
# track: my-canary

targetPort: 8080   # port the container exposes

memory: 0.25G      # minimum available memory necessary to schedule the service
cpu: 0.25          # minimum available cpu necessary to schedule the service
@@

@@forgetest/requirements.txt
flask
@@

@@forgetest/k8s/deployment.yaml
{#

This template encodes the canary deployment practices described here:

  https://kubernetes.io/docs/concepts/cluster-administration/manage-deployment/#canary-deployments

Each 'track' is a parallel deployment of the application. Each track
has it's own service named {{service.name}}-{{service.track}} that
routes directly to the specific track deployment, and {{service.name}}
is set up to route to all deployments regardless of track.

#}

{% set track = service.track|default('stable') %}
{% set canary = track != 'stable' %}
{% set name = '%s-%s' % (service.name, service.track) if canary else service.name %}

---
apiVersion: v1
kind: Service
metadata:
  name: {{name}}
spec:
  selector:
    app: {{service.name}}
{% if canary %}
    track: {{track}}
{% endif %}
  ports:
    - protocol: {{service.protocol|default('TCP')}}
      port: {{service.port|default('80')}}
      targetPort: {{service.targetPort|default('8080')}}
  type: LoadBalancer

---
# FORGE_PROFILE is {{env.FORGE_PROFILE}}
apiVersion: extensions/v1beta1
kind: Deployment
metadata: {name: {{name}}}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{service.name}}
      track: {{track}}
  strategy:
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
    type: RollingUpdate
  revisionHistoryLimit: 1
  template:
    metadata:
      labels:
        app: {{service.name}}
        track: {{track}}
      name: {{name}}
    spec:
      containers:
      - image: {{build.images["Dockerfile"]}}
        imagePullPolicy: IfNotPresent
        name: {{name}}
        resources:
          limits:
            memory: {{service.memory}}
            cpu: {{service.cpu}}
        terminationMessagePath: /dev/termination-log
      dnsPolicy: ClusterFirst
      restartPolicy: Always
      securityContext: {}
      terminationGracePeriodSeconds: 30
@@

@@forgetest/app.py
#!/usr/bin/python

import time
from flask import Flask
app = Flask(__name__)

START = time.time()

def elapsed():
    running = time.time() - START
    minutes, seconds = divmod(running, 60)
    hours, minutes = divmod(minutes, 60)
    return "%d:%02d:%02d" % (hours, minutes, seconds)

@app.route('/')
def root():
    return "forgetest-MANGLE (up %s)\n" % elapsed()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
@@

@@forgetest/subdir/EMPTY
@@
"""

user = 'forgetest'
password = 'forgetest'
org = 'forgeorg'

def launch(directory, cmd):
    return pexpect.spawn(cmd, cwd=directory, logfile=sys.stdout, timeout=120)

FORGE_YAML = """
@@forge.yaml
# Global forge configuration
# DO NOT CHECK INTO GITHUB, THIS FILE CONTAINS SECRETS
workdir: work
docker-repo: registry.hub.docker.com/forgeorg
user: forgetest
password: >
  Zm9yZ2V0ZXN0
@@
"""

def test_deploy():
    directory = mktree(FORGE_YAML + APP, MANGLE=MANGLE)
    os.environ["FORGE_PROFILE"] = "dev"
    try:
        forge = launch(directory, "forge deploy")
        forge.expect('built')
        forge.expect('forgetest/Dockerfile')
        forge.expect('pushed')
        forge.expect('forgetest-[0-9-]+:')
        forge.expect('rendered')
        forge.expect('service/forgetest-[0-9-]+')
        forge.expect('deployment.extensions/forgetest-[0-9-]+')
        forge.expect('deployed')
        forge.expect('forgetest-[0-9-]+')
        forge.expect(pexpect.EOF)
        assert forge.wait() == 0

        for sub in ("forgetest", "forgetest/subdir"):
            forge = launch(os.path.join(directory, "forgetest/subdir"), "forge deploy")
            forge.expect('rendered')
            forge.expect('service/forgetest-[0-9-]+')
            forge.expect('deployment.extensions/forgetest-[0-9-]+')
            forge.expect('deployed')
            forge.expect('forgetest-[0-9-]+')
            forge.expect(pexpect.EOF)
            assert forge.wait() == 0
    finally:
        del os.environ["FORGE_PROFILE"]

DOCKERFILES = """
@@svc/service.yaml
name: baketest
containers:
 - Dockerfile  # XXX: I think this and the following may end up colliding with the same image name.
 - dockerfile: Snowflakefile
 - dockerfile: a/Dockerfile
   context: .
 - dockerfile: b/Dockerfile
@@

@@svc/timestamp.txt
START_TIME
@@

@@svc/Dockerfile
FROM alpine:3.5
COPY timestamp.txt .
ENTRYPOINT ["echo"]
CMD ["timstamp.txt"]
@@

@@svc/Snowflakefile
FROM alpine:3.5
COPY timestamp.txt .
ENTRYPOINT ["echo"]
CMD ["timstamp.txt"]
@@

@@svc/a/Dockerfile
FROM alpine:3.5
COPY timestamp.txt .
ENTRYPOINT ["echo"]
CMD ["timstamp.txt"]
@@

@@svc/b/Dockerfile
FROM alpine:3.5
COPY b-timestamp.txt .
ENTRYPOINT ["echo"]
CMD ["b-timstamp.txt"]
@@

@@svc/b/b-timestamp.txt
START_TIME
@@
"""

def test_bake_containers():
    directory = mktree(FORGE_YAML + DOCKERFILES, START_TIME=time.ctime(START_TIME))
    forge = launch(directory, "forge -v build containers")
    forge.expect(pexpect.EOF)
    assert forge.wait() == 0

def test_no_k8s():
    directory = mktree(FORGE_YAML + "@@svc/service.yaml\nname: no_k8s\n@@")
    forge = launch(directory, "forge build manifests")
    forge.expect("k8s: template not found")
    forge.expect(pexpect.EOF)
    assert forge.wait() == 1

REBUILDER = """
@@rebuilder/service.yaml
name: rebuilder-MANGLE
containers:
  - dockerfile: Dockerfile
    rebuild:
      root: /code
      command: echo "Built on $(date)" >> buildlog.txt
      sources:
        - requirements.txt
        - src
@@

@@rebuilder/Dockerfile
FROM python:3-alpine
WORKDIR /code
COPY . ./
RUN pip install -r requirements.txt
ENTRYPOINT ["python3", "src/hello.py"]
@@

@@rebuilder/requirements.txt
click
@@

@@rebuilder/src/hello.py
print("hello")
@@
"""

REBUILDER_SUBDIR = """
@@rebuilder/service.yaml
name: rebuilder-MANGLE
containers:
  - dockerfile: Dockerfile
    rebuild:
      root: /code
      command: echo "Built on $(date)" >> buildlog.txt
      sources:
        - subdir/requirements.txt
        - subdir/src
@@

@@rebuilder/Dockerfile
FROM python:3-alpine
WORKDIR /code
COPY . ./
RUN pip install -r subdir/requirements.txt
ENTRYPOINT ["python3", "subdir/src/hello.py"]
@@

@@rebuilder/subdir/requirements.txt
click
@@

@@rebuilder/subdir/src/hello.py
print("hello")
@@
"""

def load_metadata(directory):
    result = sh("forge", "build", "metadata", cwd=directory)
    return yaml.load(result.output)

def run_image(directory):
    md = load_metadata(directory)
    image = md["build"]["images"]["Dockerfile"]
    result = sh("docker", "run", "--rm", "-it", image)
    return result.output

def do_test_rebuilder(tree, path):
    directory = mktree(FORGE_YAML + tree, MANGLE=MANGLE)
    forge = launch(directory, "forge build containers")
    forge.expect(pexpect.EOF)
    assert forge.wait() == 0
    assert run_image(directory).strip() == "hello"

    with open(os.path.join(directory, path), "write") as f:
        f.write('print("goodbye")\n')

    forge = launch(directory, "forge build containers")
    forge.expect(pexpect.EOF)
    assert forge.wait() == 0
    assert run_image(directory).strip() == "goodbye"

    forge = launch(directory, "forge clean")
    forge.expect("docker kill ")
    forge.expect(pexpect.EOF)
    assert forge.wait() == 0

def test_rebuilder():
    do_test_rebuilder(REBUILDER, "rebuilder/src/hello.py")

def test_rebuilder_subdir():
    do_test_rebuilder(REBUILDER_SUBDIR, "rebuilder/subdir/src/hello.py")
