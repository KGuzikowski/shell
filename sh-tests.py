#!/usr/bin/env python3

# You MUST NOT modify this file without author's consent.
# Doing so is considered cheating!

import os
import pexpect
import unittest
import tempfile
import random


class ShellTester():
    def setUp(self):
        self.child = pexpect.spawn('./shell')
        # self.child.interact()
        self.expect('#')

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
        self.expect('\[(\d+):(\d+)\] %s\([^)]*\)([^\r]*)\r\n' % name)
        pid, pgrp, result = self.child.match.groups()
        pid = int(pid)
        pgrp = int(pgrp)
        result = result.decode('utf-8')
        if caller is not None:
            self.assertEqual(caller, pid)
        if not result:
            return 0
        if result.startswith(' = '):
            return int(result[3:])
        if result.startswith(' -> '):
            d = {}
            for item in result[5:-1].split(', '):
                k, v = item.split('=', 1)
                try:
                    d[k] = int(v)
                except ValueError:
                    d[k] = v
            return d
        raise RuntimeError

    def expect_fork(self, parent=None):
        return self.expect_syscall('fork', caller=parent)

    def expect_execve(self, child=None):
        self.expect_syscall('execve', caller=child)

    def expect_waitpid(self, pid=None, status=None):
        while True:
            res = self.expect_syscall('waitpid')
            if res.get('pid', 0) == pid:
                break
        self.assertEqual(status, res.get('status', -1))


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
        self.expect_execve(child=child)
        self.do_quit()

        line = outf.read()
        self.assertEqual(line.split(' ')[0], str(n))

        inf.close()
        outf.close()

    def test_basic(self):
        self.sendline('sleep 10')
        child = self.expect_fork(parent=self.pid)
        self.expect_execve(child=child)
        self.sendintr()
        self.expect_waitpid(pid=child, status='SIGINT')
        self.do_quit()


if __name__ == '__main__':
    os.environ['PATH'] = '/usr/bin:/bin'
    os.environ['LD_PRELOAD'] = './trace.so'

    unittest.main()
