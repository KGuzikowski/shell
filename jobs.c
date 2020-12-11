#include "shell.h"

typedef struct proc {
  pid_t pid;    /* process identifier */
  int state;    /* RUNNING or STOPPED or FINISHED */
  int exitcode; /* -1 if exit status not yet received */
} proc_t;

typedef struct job {
  pid_t pgid;            /* 0 if slot is free */
  proc_t *proc;          /* array of processes running in as a job */
  struct termios tmodes; /* saved terminal modes */
  int nproc;             /* number of processes */
  int state;             /* changes when live processes have same state */
  char *command;         /* textual representation of command line */
} job_t;

static job_t *jobs = NULL;          /* array of all jobs */
static int njobmax = 1;             /* number of slots in jobs array */
static int tty_fd = -1;             /* controlling terminal file descriptor */
static struct termios shell_tmodes; /* saved shell terminal modes */

static void sigchld_handler(int sig) {
  int old_errno = errno;
  pid_t pid;
  int status;
  /* TODO: Change state (FINISHED, RUNNING, STOPPED) of processes and jobs.
   * Bury all children that finished saving their status in jobs. */
  while ((pid = waitpid(-1, &status, WNOHANG | WUNTRACED | WCONTINUED)) > 0) {
    bool done = false;
    size_t job_no = 0;
    for (size_t i = 0; i < njobmax; i++) {
      job_t *job = &jobs[i];
      for (size_t j = 0; j < job->nproc; j++) {
        proc_t *proc = &job->proc[j];
        if (proc->pid == pid) {
          if (WIFEXITED(status) || WIFSIGNALED(status)) {
            proc->state = FINISHED;
          } else if (WIFCONTINUED(status)) {
            proc->state = RUNNING;
          } else if (WIFSTOPPED(status)) {
            proc->state = STOPPED;
          }
          proc->exitcode = status;
          done = true;
          job_no = i;
          break;
        }
        if (done) {
          break;
        }
      }
    }

    // bool is_job_running = false;
    // bool is_job_stopped = false;
    // bool is_job_finished = false;
    // for (size_t j = 0; j < jobs[job_no].nproc; j++) {
    //   proc_t *proc = &jobs[job_no].proc[j];
    //   if (proc->state == RUNNING) {
    //     is_job_running = true;
    //   } else if (proc->state == STOPPED) {
    //     is_job_stopped = true;
    //   } else if (proc->state == FINISHED) {
    //     is_job_finished = true;
    //   }
    // }

    // if (is_job_finished && !is_job_running && !is_job_stopped) {
    //   jobs[job_no].state = FINISHED;
    // } else if (is_job_stopped && !is_job_running && !is_job_finished) {
    //   jobs[job_no].state = STOPPED;
    // } else if (is_job_running && !is_job_finished && !is_job_stopped) {
    //   jobs[job_no].state = RUNNING;
    // }
    unsigned int mask = 0;
    job_t *job = &jobs[job_no];
    for (int i = 0; i < job->nproc; i++) {
      switch (job->proc[i].state) {
        case FINISHED: {
          mask = mask | 1;
          break;
        }
        case STOPPED: {
          mask = mask | 2;
          break;
        }
        case RUNNING: {
          mask = mask | 4;
          break;
        }
      }
    }
    switch (mask) {
      case 1: {
        job->state = FINISHED;
        break;
      }
      case 2: {
        job->state = STOPPED;
        break;
      }
      case 4: {
        job->state = RUNNING;
        break;
      }
      default: {
        break;
      }
    }
  }
  errno = old_errno;
}

/* When pipeline is done, its exitcode is fetched from the last process. */
static int exitcode(job_t *job) {
  return job->proc[job->nproc - 1].exitcode;
}

static int allocjob(void) {
  /* Find empty slot for background job. */
  for (int j = BG; j < njobmax; j++)
    if (jobs[j].pgid == 0)
      return j;

  /* If none found, allocate new one. */
  jobs = realloc(jobs, sizeof(job_t) * (njobmax + 1));
  memset(&jobs[njobmax], 0, sizeof(job_t));
  return njobmax++;
}

static int allocproc(int j) {
  job_t *job = &jobs[j];
  job->proc = realloc(job->proc, sizeof(proc_t) * (job->nproc + 1));
  return job->nproc++;
}

int addjob(pid_t pgid, int bg) {
  int j = bg ? allocjob() : FG;
  job_t *job = &jobs[j];
  /* Initial state of a job. */
  job->pgid = pgid;
  job->state = RUNNING;
  job->command = NULL;
  job->proc = NULL;
  job->nproc = 0;
  job->tmodes = shell_tmodes;
  return j;
}

static void deljob(job_t *job) {
  assert(job->state == FINISHED);
  free(job->command);
  free(job->proc);
  job->pgid = 0;
  job->command = NULL;
  job->proc = NULL;
  job->nproc = 0;
}

static void movejob(int from, int to) {
  assert(jobs[to].pgid == 0);
  memcpy(&jobs[to], &jobs[from], sizeof(job_t));
  memset(&jobs[from], 0, sizeof(job_t));
}

static void mkcommand(char **cmdp, char **argv) {
  if (*cmdp)
    strapp(cmdp, " | ");

  for (strapp(cmdp, *argv++); *argv; argv++) {
    strapp(cmdp, " ");
    strapp(cmdp, *argv);
  }
}

void addproc(int j, pid_t pid, char **argv) {
  assert(j < njobmax);
  job_t *job = &jobs[j];

  int p = allocproc(j);
  proc_t *proc = &job->proc[p];
  /* Initial state of a process. */
  proc->pid = pid;
  proc->state = RUNNING;
  proc->exitcode = -1;
  mkcommand(&job->command, argv);
}

/* Returns job's state.
 * If it's finished, delete it and return exitcode through statusp. */
int jobstate(int j, int *statusp) {
  assert(j < njobmax);
  job_t *job = &jobs[j];
  int state = job->state;

  /* TODO: Handle case where job has finished. */
  if (state == FINISHED) {
    *statusp = exitcode(job);
    deljob(job);
  }

  return state;
}

char *jobcmd(int j) {
  assert(j < njobmax);
  job_t *job = &jobs[j];
  return job->command;
}

/* Continues a job that has been stopped. If move to foreground was requested,
 * then move the job to foreground and start monitoring it. */
bool resumejob(int j, int bg, sigset_t *mask) {
  if (j < 0) {
    for (j = njobmax - 1; j > 0 && jobs[j].state == FINISHED; j--)
      continue;
  }

  if (j >= njobmax || jobs[j].state == FINISHED)
    return false;

  /* TODO: Continue stopped job. Possibly move job to foreground slot. */
  if (jobs[j].state == RUNNING && bg) {
    return true;
  }

  /* Give controll to the saved foreground job. */
  killpg(jobs[j].pgid, SIGCONT);

  if (!bg && jobs[FG].pgid == 0) {
    movejob(j, FG);
    Tcsetpgrp(tty_fd, jobs[FG].pgid);
    Tcgetattr(tty_fd, &jobs[FG].tmodes);
    if (jobs[0].state == STOPPED) {
      killpg(jobs[FG].pgid, SIGCONT);
      while (jobs[FG].state == STOPPED) {
        Sigsuspend(mask);
      }
    }
    msg("[%d] continue '%s'\n", j, jobcmd(FG));
    monitorjob(mask);
  } else {
    msg("[%d] continue '%s'\n", j, jobcmd(j));
  }

  return true;
}

/* Kill the job by sending it a SIGTERM. */
bool killjob(int j) {
  if (j >= njobmax || jobs[j].state == FINISHED)
    return false;
  debug("[%d] killing '%s'\n", j, jobcmd(j));

  /* TODO: I love the smell of napalm in the morning. */
  /* Resume stopped jobs to kill them. */
  killpg(jobs[j].pgid, SIGTERM);
  killpg(jobs[j].pgid,
         SIGCONT); /*This will make sure that stopped processes get killed.*/

  return true;
}

/* Report state of requested background jobs. Clean up finished jobs. */
void watchjobs(int which) {
  for (int j = BG; j < njobmax; j++) {
    if (jobs[j].pgid == 0)
      continue;

    /* TODO: Report job number, state, command and exit code or signal. */
    int state, exitcode = -1;
    /* if jobs is FINISHED it will be cleaned in jobstate */
    char *command = strdup(jobcmd(j));
    state = jobstate(j, &exitcode);
    if (which == ALL) {
      /* As do_jobs say, displaying FINISHED jobs is not wanted. */
      if (state == RUNNING) {
        msg("[%d] running '%s'\n", j, command);
      } else if (state == STOPPED) {
        msg("[%d] suspended '%s'\n", j, command);
      } else if (state == FINISHED) {
        if (WIFEXITED(exitcode)) {
          msg("[%d] exited '%s', status=%d\n", j, command,
              WEXITSTATUS(exitcode));
        } else {
          msg("[%d] killed '%s' by signal %d\n", j, command,
              WTERMSIG(exitcode));
        }
      }
    } else if (which == FINISHED && state == FINISHED) {
      if (WIFEXITED(exitcode)) {
        msg("[%d] exited '%s', status=%d\n", j, command, WEXITSTATUS(exitcode));
      } else {
        msg("[%d] killed '%s' by signal %d\n", j, command, WTERMSIG(exitcode));
      }
    }
    free(command);
  }
}

/* Monitor job execution. If it gets stopped move it to background.
 * When a job has finished or has been stopped move shell to foreground. */
int monitorjob(sigset_t *mask) {
  int exitcode = 0, state;

  /* TODO: Following code requires use of Tcsetpgrp of tty_fd. */
  /* Save current pgid of foreground job. */
  Tcsetpgrp(tty_fd, jobs[FG].pgid);
  while (true) {
    state = jobstate(FG, &exitcode);
    if (state == STOPPED) {
      int new_bg_index = allocjob();
      movejob(FG, new_bg_index);
      msg("[%d] suspended '%s'\n", new_bg_index, jobs[new_bg_index].command);
      break;
    } else if (state == FINISHED) {
      break;
    }
    Sigsuspend(mask);
  }
  /* Give controll to the saved foreground job. */
  if (state != RUNNING) {
    Tcsetpgrp(tty_fd, getpgid(FG));
    Tcgetattr(tty_fd, &shell_tmodes);
  }

  return exitcode;
}

/* Called just at the beginning of shell's life. */
void initjobs(void) {
  Signal(SIGCHLD, sigchld_handler);
  jobs = calloc(sizeof(job_t), 1);

  /* Assume we're running in interactive mode, so move us to foreground.
   * Duplicate terminal fd, but do not leak it to subprocesses that execve. */
  assert(isatty(STDIN_FILENO));
  tty_fd = Dup(STDIN_FILENO);
  fcntl(tty_fd, F_SETFD, FD_CLOEXEC);

  /* Take control of the terminal. */
  Tcsetpgrp(tty_fd, getpgrp());

  /* Save default terminal attributes for the shell. */
  Tcgetattr(tty_fd, &shell_tmodes);
}

/* Called just before the shell finishes. */
void shutdownjobs(void) {
  sigset_t mask;
  Sigprocmask(SIG_BLOCK, &sigchld_mask, &mask);

  /* TODO: Kill remaining jobs and wait for them to finish. */
  for (int i = 0; i < njobmax; i++) {
    if (killjob(i)) {
      while (FINISHED != jobs[i].state) {
        sigsuspend(&mask);
      }
    }
  }

  watchjobs(FINISHED);

  Sigprocmask(SIG_SETMASK, &mask, NULL);

  Close(tty_fd);
}
