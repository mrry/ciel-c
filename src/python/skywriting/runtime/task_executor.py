# Copyright (c) 2010 Derek Murray <derek.murray@cl.cam.ac.uk>
#                    Christopher Smowton <chris.smowton@cl.cam.ac.uk>
#
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
from __future__ import with_statement
from skywriting.lang.parser import CloudScriptParser
import urlparse
from skywriting.runtime.plugins import AsynchronousExecutePlugin
from skywriting.lang.context import SimpleContext, TaskContext,\
    LambdaFunction
from skywriting.lang.visitors import \
    StatementExecutorVisitor, SWDereferenceWrapper
from skywriting.lang import ast
from skywriting.runtime.exceptions import ReferenceUnavailableException,\
    FeatureUnavailableException, ExecutionInterruption,\
    SelectException, MissingInputException, MasterNotRespondingException,\
    RuntimeSkywritingError, BlameUserException
from skywriting.runtime.references import SWReferenceJSONEncoder
from threading import Lock
import cherrypy
import logging
import uuid
import hashlib
import subprocess
import pickle
import simplejson
import os.path
from shared.references import SWDataValue, SWRealReference,\
    SWErrorReference, SW2_FutureReference, SW2_ConcreteReference
from shared.exec_helpers import get_exec_prefix, get_exec_output_ids

class TaskExecutorPlugin(AsynchronousExecutePlugin):
    
    def __init__(self, bus, skypybase, block_store, master_proxy, execution_features, num_threads=1):
        AsynchronousExecutePlugin.__init__(self, bus, num_threads, "execute_task")
        self.block_store = block_store
        self.master_proxy = master_proxy
        self.execution_features = execution_features
        self.skypybase = skypybase

        self.root_executor = None
        self._lock = Lock()

        self.reset()
    
    # Out-of-thread asynchronous notification calls

    def abort_task(self, task_id):
        with self._lock:
            if self.current_task_id == task_id:
                self.current_task_execution_record.abort()
            self.current_task_id = None
            self.current_task_execution_record = None

    def notify_streams_done(self, task_id):
        with self._lock:
            # Guards against changes to self.current_{task_id, task_execution_record}
            if self.root_task_id == task_id:
                # Note on threading: much like aborts, the execution_record's execute() is running
                # in another thread. It might have not yet begun, already completed, or be in progress.
                self.root_executor.notify_streams_done()
    
    # Helper functions for main

    def run_task_with_executor(self, task_descriptor, executor)
        cherrypy.engine.publish("worker_event", "Start execution " + repr(input['task_id']) + " with handler " + input['handler'])
        cherrypy.log.error("Starting task %s with handler %s" % (str(input['task_id']), new_task_handler), 'TASK', logging.INFO, False)
        try:
            executor.run(input)
            cherrypy.engine.publish("worker_event", "Completed execution " + repr(input['task_id']))
            cherrypy.log.error("Completed task %s with handler %s" % (str(input['task_id']), new_task_handler), 'TASK', logging.INFO, False)
        except:
            cherrypy.log.error("Error in task %s with handler %s" % (str(input['task_id']), new_task_handler), 'TASK', logging.ERROR, True)

    def spawn_all(self):
        if len(self.spawned_tasks) == 0:
            return
        master_proxy.spawn_tasks(self.root_task_id, self.spawned_tasks)

    def create_spawned_task_name(self):
        sha = hashlib.sha1()
        sha.update('%s:%d' % (self.task_id, self.spawn_counter))
        ret = sha.hexdigest()
        self.spawn_counter += 1
        return ret

    def commit(self):
        commit_bindings = dict([(ref.id, ref) for ref in self.published_refs])
        self.task_executor.master_proxy.commit_task(self.task_id, commit_bindings)

    def reset(self):
        self.published_refs = []
        self.spawned_tasks = []
        self.reference_cache = None
        self.task_for_output_id = dict()
        self.spawn_counter = 0
        with self._lock:
            self.root_task_id = None

    # Main entry point

    def handle_input(self, input):

        new_task_handler = input["handler"]
        with self._lock:
            try:
                if self.root_executor.handler != new_task_handler:
                    self.root_executor = None
            except AttributeError:
                pass
            if self.root_executor is None:
                self.root_executor = self.execution_features.get_executor(executor_name, self)
            self.root_task_id = input["task_id"]

        self.reference_cache = input["inputs"]
        self.run_task_with_executor(input, self.root_executor)
        self.commit()
        self.spawn_all()
        self.reset()

    # Callbacks for executors

    def publish_ref(self, ref):
        self.published_refs.append(ref)
        self.reference_cache[ref.id] = ref

    def spawn_task(self, new_task_descriptor, **args):
        new_task_descriptor["task_id"] = self.create_spawned_task_name()
        if "dependencies" not in new_task_descriptor:
            new_task_descriptor["dependencies"] = {}
        target_executor = self.execution_features.get_executor(new_task_descriptor["handler"], self)
        # Throws a BlameUserException if we can quickly determine the task descriptor is bad
        target_executor.build_task_descriptor(new_task_descriptor, **args)
        # TODO here: use the master's task-graph apparatus.
        if "hint_small_task" in new_task_descriptor:
            for output in new_task_descriptor['expected_outputs']:
                task_for_output_id[output] = new_task_descriptor
        self.spawned_tasks.append(new_task_descriptor)
        return new_task_descriptor

    def resolve_ref(self, ref):
        if ref.is_consumable():
            return ref
        else:
            try:
                return self.reference_cache[ref.id]
            except KeyError:
                raise ReferenceUnavailableException(ref)

    def retrieve_ref(self, ref):
        try:
            return self.resolve_ref(ref)
        except ReferenceUnavailableException as e:
            # Try running a small task to generate the required reference
            try:
                producer_task = task_for_output_id[id]
                # Presence implies hint_small_task: we should run this now
            except KeyError:
                raise e
            # Try to resolve all the child's dependencies
            try:
                producer_task["inputs"] = dict()
                for child_ref in producer_task["dependencies"]:
                    producer_task["inputs"][child_ref.id] = self.resolve_ref(child_ref)
            except ReferenceUnavailableException:
                # Child can't run now
                del producer_task["inputs"]
                raise e
            nested_executor = self.execution_features.get_executor(producer_task["handler"], self)
            self.run_task_with_executor(producer_task, nested_executor)
            # Okay the child has run, and may or may not have defined its outputs.
            # If it hasn't, this will throw appropriately
            return self.resolve_ref(ref)

