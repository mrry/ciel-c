
from __future__ import with_statement

import stackless
import traceback
import pickle
import simplejson
from contextlib import closing
from StringIO import StringIO

from shared.io_helpers import MaybeFile
from shared.references import encode_datavalue, decode_datavalue_string,\
    json_decode_object_hook

from file_outputs import OutputFile
from ref_fetch import CompleteFile, StreamingFile

### Constants

HALT_REFERENCE_UNAVAILABLE = 1
HALT_DONE = 2
HALT_RUNTIME_EXCEPTION = 3

### Helpers

class PersistentState:
    def __init__(self):
        self.ref_dependencies = dict()

class ResumeState:
    
    def __init__(self, pstate, coro):
        self.coro = coro
        self.persistent_state = pstate
        
class PackageKeyError(Exception):
    def __init__(self, key):
        Exception.__init__(self)
        self.key = key
        
def describe_maybe_file(output_fp, out_dict):
    if output_fp.real_fp is not None:
        out_dict["filename"] = output_fp.filename
        output_fp.real_fp.close()
    else:
        out_dict["strdata"] = encode_datavalue(output_fp.str)
        
def ref_from_maybe_file(output_fp, refidx, message_helper):
    if output_fp.real_fp is not None:
        return output_fp.real_fp.get_completed_ref()
    else:
        args = {"index": refidx, "str": encode_datavalue(output_fp.str)}
        return message_helper.synchronous_request("publish_string", args)["ref"]
    
def start_script(entry_point, entry_args):

    global halt_reason
    global script_return_val
    global script_backtrace

    try:
        script_return_val = entry_point(*entry_args)
        halt_reason = HALT_DONE
    except Exception, e:
        script_return_val = e
        script_backtrace = traceback.format_exc()
        halt_reason = HALT_RUNTIME_EXCEPTION
        
    current_task.main_coro.switch()
    
### Task state

current_task = None

class SkyPyTask:
    
    def __init__(self, main_coro, persistent_state, ret_output, other_outputs, message_helper, file_outputs):
        
        self.main_coro = main_coro
        self.persistent_state = persistent_state
        self.ret_output = ret_output
        self.other_outputs = other_outputs
        self.message_helper = message_helper
        self.file_outputs = file_outputs
        self.ref_cache = dict()
        self.script_return_val = None
        self.script_backtrace = None
        self.halt_reason = 0

def fetch_ref(ref, verb, **kwargs):

    if ref.id in current_task.ref_cache:
        return current_task.ref_cache[ref.id]
    else:
        for tries in range(2):
            add_ref_dependency(ref)
            send_dict = {"ref": ref}
            send_dict.update(kwargs)
            runtime_response = current_task.message_helper.synchronous_request(verb, send_dict)
            if "error" in runtime_response:
                if tries == 0:
                    current_task.halt_reason = HALT_REFERENCE_UNAVAILABLE
                    current_task.main_coro.switch()
                    continue
                else:
                    raise Exception("Double failure trying to deref %s" % ref.id)
            remove_ref_dependency(ref)
            # We're back -- the ref should be available now.
            return runtime_response

def deref_json(ref):
    
    runtime_response = fetch_ref(ref, "open_ref")
    try:
        obj = simplejson.loads(decode_datavalue_string(runtime_response["strdata"]), object_hook=json_decode_object_hook)
    except KeyError:
        with open(runtime_response["filename"], "r") as ref_fp:
            obj = simplejson.load(ref_fp, object_hook=json_decode_object_hook)
    current_task.ref_cache[ref.id] = obj
    return obj

def deref(ref):

    runtime_response = fetch_ref(ref, "open_ref")
    try:
        obj = pickle.loads(decode_datavalue_string(runtime_response["strdata"]))
    except KeyError:
        with open(runtime_response["filename"], "r") as ref_fp:
            obj = pickle.load(ref_fp)
    current_task.ref_cache[ref.id] = obj
    return obj

def add_ref_dependency(ref):
    if not ref.is_consumable():
        try:
            current_task.persistent_state.ref_dependencies[ref.id] += 1
        except KeyError:
            current_task.persistent_state.ref_dependencies[ref.id] = 1

def remove_ref_dependency(ref):
    if not ref.is_consumable():
        current_task.persistent_state.ref_dependencies[ref.id] -= 1
        if current_task.persistent_state.ref_dependencies[ref.id] == 0:
            del current_task.persistent_state.ref_dependencies[ref.id]

def save_state(state):

    state_index = get_fresh_output_index(prefix="coro")
    state_fp = MaybeFile(open_callback=lambda: open_output(state_index))
    with state_fp:
        pickle.dump(state, state_fp)
    return ref_from_maybe_file(state_fp, state_index, current_task.message_helper)

def spawn(spawn_callable, *pargs, **kwargs):
    
    new_coro = stackless.coroutine()
    new_coro.bind(start_script, spawn_callable, pargs)
    save_obj = ResumeState(None, new_coro)
    coro_ref = save_state(save_obj)
    return do_spawn("skypy", False, pyfile_ref=current_task.persistent_state.py_ref, coro_ref=coro_ref, **kwargs)

def do_spawn(executor_name, small_task, **args):
    
    args["small_task"] = small_task
    args["executor_name"] = executor_name
    response = current_task.message_helper.synchronous_request("spawn", args)
    return response

def spawn_exec(executor_name, **args):
    return do_spawn(executor_name, False, **args)

def sync_exec(executor_name, **args):
    return do_spawn(executor_name, True, **args)

def package_lookup(key):
    
    response = current_task.message_helper.synchronous_request("package_lookup", {"key": key})
    retval = response["value"]
    if retval is None:
        raise PackageKeyError(key)
    return retval

def deref_as_raw_file(ref, may_stream=False, sole_consumer=False, chunk_size=67108864):
    if not may_stream:
        runtime_response = fetch_ref(ref, "open_ref")
        try:
            return closing(StringIO(decode_datavalue_string(runtime_response["strdata"])))
        except KeyError:
            return CompleteFile(ref, runtime_response["filename"])
    else:
        runtime_response = fetch_ref(ref, "open_ref_async", chunk_size=chunk_size, sole_consumer=sole_consumer)
        if runtime_response["done"]:
            return CompleteFile(ref, runtime_response["filename"])
        elif runtime_response["blocking"]:
            return CompleteFile(ref, runtime_response["filename"], chunk_size=chunk_size, must_close=True)
        else:
            return StreamingFile(ref, runtime_response["filename"], runtime_response["size"], chunk_size)

def get_fresh_output_index(prefix=""):
    runtime_response = current_task.message_helper.synchronous_request("allocate_output", {"prefix": prefix})
    return runtime_response["index"]

def open_output(index, may_pipe=False):
    new_output = OutputFile(current_task.message_helper, current_task.file_outputs, index)
    runtime_response = current_task.message_helper.synchronous_request("open_output", {"index": index, "may_pipe": may_pipe})
    new_output.set_filename(runtime_response["filename"])
    return new_output
    
class RequiredRefs():
    def __init__(self, refs):
        self.refs = refs

    def __enter__(self):
        for ref in self.refs:
            current_task.add_ref_dependency(ref)

    def __exit__(self, x, y, z):
        for ref in self.refs:
            current_task.remove_ref_dependency(ref)

    
