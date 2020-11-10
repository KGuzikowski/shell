#!/usr/bin/env python3

import os
import re
import errno
import time
import subprocess
import pexpect
import unittest
import sys
import tempfile
import random


class ShellTester():
    def setUp(self):
        self.child = pexpect.spawn('./shell')
        self.child.setecho(False)

        self.strace = pexpect.spawn(
                'strace -f -q '
                '-e trace=ioctl,clone,setpgid,execve,dup2,openat,close '
                '-p ' + str(self.child.pid), encoding='utf-8')
        # self.strace.logfile = sys.stderr

        time.sleep(0.1)

        self.sendline(' ')
        self.expect('#')

    def tearDown(self):
        self.strace.close()

    @property
    def pid(self):
        return self.child.pid

    def sendline(self, s):
        self.child.sendline(s)

    def sendintr(self):
        self.child.sendintr()

    def expect(self, s):
        self.child.expect(s)

    def wait(self):
        self.child.wait()

    def expect_syscall(self, name, caller=None):
        self.strace.expect('(\[pid\s+\d+\])? ?%s\([^)]+\) = (-?\d+)\r\n' % name)
        pid, result = self.strace.match.groups()
        if pid is None:
            pid = self.child.pid
        else:
            mo = re.match(r'\[pid\s+(\d+)\]', pid)
            assert mo is not None
            pid = int(mo.group(1))
        if caller is not None:
            self.assertEqual(caller, pid)
        return int(result)

    def expect_fork(self, parent=None):
        return self.expect_syscall('clone', caller=parent)

    def expect_execve(self, pid=None, error=None):
        self.assertEqual(self.expect_syscall('execve', caller=pid), error)

    def expect_sigchld(self, pid=None, status=None):
        self.strace.expect('--- SIGCHLD {([^}]+)} ---\r\n')
        siginfo = {}
        for pair in self.strace.match.group(1).split(', '):
            k, v = pair.split('=')
            try:
                siginfo[k] = int(v)
            except ValueError:
                siginfo[k] = v
        if pid is not None:
            self.assertEqual(siginfo['si_pid'], pid)
        if status is not None:
            self.assertEqual(siginfo['si_status'], status)
        return siginfo


class TestShell(ShellTester, unittest.TestCase):
    def do_quit(self):
        self.expect('#')
        self.sendline('quit')
        self.wait()

    def test_redir_1(self):
        inf = tempfile.NamedTemporaryFile(mode='w')
        outf = tempfile.NamedTemporaryFile(mode='r')

        n = random.randrange(100, 200)
        for i in range(n):
            inf.write('a\n')
        inf.flush()

        self.sendline('wc -l ' + inf.name + ' >' + outf.name)
        child = self.expect_fork(parent=self.pid)
        self.expect_execve(pid=child, error=0)
        self.do_quit()

        line = outf.read()
        self.assertEqual(line.split(' ')[0], str(n))

        inf.close()
        outf.close()

    def test_basic(self):
        self.sendline('sleep 10')
        child = self.expect_fork(parent=self.pid)
        self.expect_execve(pid=child, error=0)
        self.sendintr()
        self.expect_sigchld(pid=child, status='SIGINT')
        self.do_quit()


if __name__ == '__main__':
    os.environ['PATH'] = '/usr/bin:/bin'

    unittest.main()
