#!/usr/bin/env python3

# You MUST NOT modify this file without author's consent.
# Doing so is considered cheating!

import os
import pexpect
import unittest
import random
import time
from tempfile import NamedTemporaryFile


class ShellTesterSimple():
    def setUp(self):
        self.child = pexpect.spawn('./shell')
        self.child.setecho(False)
        # self.child.interact()
        self.expect('#')

    def lines_before(self):
        return self.child.before.decode('utf-8').strip('\r\n').split('\r\n')

    def lines_after(self):
        return self.child.after.decode('utf-8').strip('\r\n').split('\r\n')

    def sendline(self, s):
        self.child.sendline(s)

    def sendintr(self):
        self.child.sendintr()

    def expect(self, s):
        self.child.expect(s)

    def expect_exact(self, s):
        self.child.expect_exact(s)

    @property
    def pid(self):
        return self.child.pid

    def wait(self):
        self.child.wait()


class ShellTester(ShellTesterSimple):
    def setUp(self):
        os.environ['LD_PRELOAD'] = './trace.so'
        self.child = pexpect.spawn('./shell')
        self.child.setecho(False)
        self.expect('#')

    def tearDown(self):
        del os.environ['LD_PRELOAD']

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

    def expect_exit(self, status=None):
        child = self.expect_fork(parent=self.pid)
        self.expect_execve(child=child)
        self.expect_waitpid(pid=child, status=status)
        self.expect('#')


class TestShellSimple(ShellTesterSimple, unittest.TestCase):
    def execute(self, cmd):
        self.sendline(cmd)
        self.expect('#')
        return self.lines_before()

    def test_redir_1(self):
        nlines = 587
        inf_name = 'include/queue.h'

        # 'wc -l include/queue.h > out'
        with NamedTemporaryFile(mode='r') as outf:
            self.sendline('wc -l ' + inf_name + ' >' + outf.name)
            self.expect('#')
            self.assertEqual(int(outf.read().split()[0]), nlines)

        # 'wc -l < include/queue.h'
        self.sendline('wc -l < ' + inf_name)
        self.expect(str(nlines))
        self.expect('#')

        # 'wc -l < include/queue.h > out'
        with NamedTemporaryFile(mode='r') as outf:
            self.sendline('wc -l < ' + inf_name + ' >' + outf.name)
            self.expect('#')
            self.assertEqual(int(outf.read().split()[0]), nlines)

    def test_redir_2(self):
        with NamedTemporaryFile(mode='w') as inf:
            with NamedTemporaryFile(mode='r') as outf:
                n = random.randrange(100, 200)

                for i in range(n):
                    inf.write('a\n')
                inf.flush()

                # 'wc -l < random-text > out'
                self.sendline('wc -l ' + inf.name + ' >' + outf.name)
                self.expect('#')
                self.assertEqual(outf.read().split()[0], str(n))

    def test_pipeline_1(self):
        self.sendline('grep LIST include/queue.h | wc -l')
        self.expect('46')
        self.expect('#')

    def test_pipeline_2(self):
        self.sendline('cat include/queue.h | cat | grep LIST | cat | wc -l')
        self.expect('46')
        self.expect('#')

    def test_pipeline_2(self):
        with NamedTemporaryFile(mode='r') as outf:
            self.sendline(
                    'cat < include/queue.h | grep LIST | wc -l > ' + outf.name)
            self.expect('#')
            self.assertEqual(int(outf.read().split()[0]), 46)

    def test_leaks(self):
        # 'ls -l /proc/self/fd'
        lines = self.execute('ls -l /proc/self/fd')
        self.assertEqual(len(lines), 5)
        for i in range(3):
            self.assertIn('%d -> /dev/pts/' % i, lines[i + 1])
        self.assertIn('3 -> /proc/', lines[4])

        # 'ls -l /proc/self/fd | cat'
        lines = self.execute('ls -l /proc/self/fd | cat')
        self.assertEqual(len(lines), 5)
        self.assertIn('pipe:', lines[2])

        # 'echo test | ls -l /proc/self/fd'
        lines = self.execute('echo test | ls -l /proc/self/fd')
        self.assertEqual(len(lines), 5)
        self.assertIn('pipe:', lines[1])

        # 'echo test | ls -l /proc/self/fd | cat'
        lines = self.execute('echo test | ls -l /proc/self/fd | cat')
        self.assertEqual(len(lines), 5)
        self.assertIn('pipe:', lines[1])
        self.assertIn('pipe:', lines[2])

        # check shell 'ls -l /proc/$pid/fd'
        lines = self.execute('ls -l /proc/%d/fd' % self.pid)
        self.assertEqual(len(lines), 5)
        for i in range(4):
            self.assertIn('%d -> /dev/pts/' % i, lines[i + 1])

    def test_exitcode_1(self):
        # 'true &'
        self.sendline('true &')
        self.expect_exact("running 'true'")
        self.sendline('jobs')
        self.expect_exact("exited 'true', status=0")

        # 'false &'
        self.sendline('false &')
        self.expect_exact("running 'false'")
        self.sendline('jobs')
        self.expect_exact("exited 'false', status=1")

        if False:
            # 'exit 42 &'
            self.sendline('exit 42 &')
            self.expect_exact("running 'exit 42'")
            self.sendline('jobs')
            self.expect_exact("exited 'exit 42', status=42")

    def test_kill_suspended(self):
        self.sendline('cat &')
        self.expect_exact("running 'cat'")
        self.sendline('jobs')
        self.expect_exact("suspended 'cat'")
        self.sendline('pkill -9 cat')
        self.expect_exact("killed 'cat' by signal 9")

    def test_resume_suspended(self):
        self.sendline('cat &')
        self.expect_exact("running 'cat'")
        self.sendline('jobs')
        self.expect_exact("suspended 'cat'")
        self.sendline('fg')
        self.expect_exact("continue 'cat'")
        self.sendintr()
        self.sendline('jobs')
        # expect something ?

    def test_kill_jobs(self):
        self.sendline('sleep 1000 &')
        self.expect_exact("[1] running 'sleep 1000'")
        self.sendline('sleep 2000 &')
        self.expect_exact("[2] running 'sleep 2000'")
        self.sendline('jobs')
        self.expect_exact("[1] running 'sleep 1000'")
        self.expect_exact("[2] running 'sleep 2000'")
        self.sendline('kill %2')
        self.sendline('jobs')
        self.expect_exact("[1] running 'sleep 1000'")
        self.expect_exact("[2] killed 'sleep 2000' by signal 15")
        self.sendline('kill %1')
        self.sendline('jobs')
        self.expect_exact("[1] killed 'sleep 1000' by signal 15")


class TestShellWithSyscalls(ShellTester, unittest.TestCase):
    def test_quit(self):
        self.sendline('quit')
        self.wait()

    def test_sigint(self):
        self.sendline('sleep 10')
        child = self.expect_fork(parent=self.pid)
        self.expect_execve(child=child)
        self.sendintr()
        self.expect_waitpid(pid=child, status='SIGINT')
        self.expect('#')


if __name__ == '__main__':
    os.environ['PATH'] = '/usr/bin:/bin'
    os.environ['LC_ALL'] = 'C'

    unittest.main()
