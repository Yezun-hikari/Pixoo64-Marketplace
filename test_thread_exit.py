import threading
import time
import sys

def thread_func():
    try:
        while True:
            try:
                print("Thread running")
                raise SystemExit()
            except Exception as e:
                print("Caught exception", e)
            time.sleep(1)
    except BaseException as e:
        print("Caught base exception in thread:", type(e))
        raise

t = threading.Thread(target=thread_func)
t.start()
time.sleep(2)
print("Main thread still alive")
