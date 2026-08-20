"""
Standalone Test Runner & Logger for Instagram Pro Downloader.
Executes test suite, formats execution results, and saves detailed log files.
Usage: python run_tests.py
"""

import datetime
import io
import os
import sys
import unittest


class DualOutputTee:
    """Redirects writes to both standard console output and a dedicated log file."""

    def __init__(self, console_stream, file_stream):
        self.console_stream = console_stream
        self.file_stream = file_stream

    def write(self, message):
        self.console_stream.write(message)
        self.console_stream.flush()
        self.file_stream.write(message)
        self.file_stream.flush()

    def flush(self):
        self.console_stream.flush()
        self.file_stream.flush()


def run_suite():
    # 1. Setup logs directory and timestamped target file
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"test_run_{timestamp}.log"
    log_path = os.path.join(log_dir, log_filename)

    # 2. Discover tests from the /tests directory
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")

    with open(log_path, "w", encoding="utf-8") as log_file:
        dual_stream = DualOutputTee(sys.stdout, log_file)

        # Write execution header
        dual_stream.write("=======================================================\n")
        dual_stream.write(" 🚀 INSTAGRAM PRO DOWNLOADER - TEST SUITE RUNNER\n")
        dual_stream.write(f" 📅 Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        dual_stream.write(f" 📁 Log File : {log_path}\n")
        dual_stream.write("=======================================================\n\n")

        # Execute tests via TextTestRunner targeting the dual stream
        runner = unittest.TextTestRunner(stream=dual_stream, verbosity=2)
        start_time = datetime.datetime.now()
        result = runner.run(suite)
        elapsed = (datetime.datetime.now() - start_time).total_seconds()

        # Write summary report
        dual_stream.write("\n=======================================================\n")
        dual_stream.write(f" ⏱️  Duration      : {elapsed:.2f} seconds\n")
        dual_stream.write(f" 🧪 Total Tests   : {result.testsRun}\n")
        dual_stream.write(f" ✅ Passed        : {result.testsRun - len(result.failures) - len(result.errors)}\n")
        dual_stream.write(f" ❌ Failures      : {len(result.failures)}\n")
        dual_stream.write(f" ⚠️  Errors        : {len(result.errors)}\n")
        
        if result.wasSuccessful():
            dual_stream.write(" 🎉 STATUS        : ALL TESTS PASSED SUCCESSFULLY\n")
        else:
            dual_stream.write(" 🚨 STATUS        : CRITICAL TEST FAILURES DETECTED\n")
        dual_stream.write("=======================================================\n")

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_suite()
    sys.exit(0 if success else 1)