#!/usr/bin/env python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import print_function, unicode_literals

from multiprocessing import cpu_count
from Queue import Full
import os
import shutil
import sys
import time
import uuid

from mozillapulse.consumers import NormalizedBuildConsumer
import mongoengine

from .config import platforms
from .worker import (
    Worker,
    build_queue,
    tests_cache
)

num_workers = cpu_count()

def on_build_event(data, message):
    # ack the message to remove it from the queue
    message.ack()
    payload = data['payload']

    # only look at nightly builds
    #if 'nightly' not in payload['tags']:
    #    return

    # skip l10n builds
    if 'l10n' in payload['tags']:
        return

    # skip builds without any supported suites running against them
    if (payload['platform'], payload['buildtype']) not in platforms:
        return

    try:
        build_queue.put(payload, block=False)
    except Full:
        # if backlog is too big, discard oldest build
        # TODO discard platforms for which we have the most data
        discarded = build_queue.get()
        print("Did not process buildid '{}', backlog too big!".format(discarded['buildid']))
        build_queue.put(payload, block=False)


def run():
    # Connect to db
    mongoengine.connect('test-informant')

    # Start worker threads
    for _ in range(num_workers):
        worker = Worker()
        worker.daemon = True
        worker.start()

    # Connect to pulse
    label = 'test-informant-{}'.format(uuid.uuid4())
    topic = 'build.mozilla-inbound.#'
    pulse = NormalizedBuildConsumer(applabel=label)
    pulse.configure(topic=topic, callback=on_build_event)

    try:
        while True:
            print("Listening on '{}'...".format(topic))
            try:
                pulse.listen()
            except IOError: # sometimes socket gets closed
                pass
    except KeyboardInterrupt:
        print("Waiting for threads to finish processing, press Ctrl-C again to exit now...")
        try:
            # do this instead of Queue.join() so KeyboardInterrupts get caught
            while build_queue.unfinished_tasks:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(1)
    finally:
        print("Threads finished, performing final cleanup...")
        # clean up leftover tests bundles
        for v in tests_cache.values():
            if v and os.path.isdir(v):
                shutil.rmtree(v)


if __name__ == "__main__":
    sys.exit(run())
