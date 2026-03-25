/*
 * memo-transcriber launcher
 *
 * Thin wrapper binary so macOS Full Disk Access can be granted to a
 * specific, dedicated binary rather than to /bin/bash globally.
 *
 * The wrapper forks, execs bash with the arguments passed via the
 * launchd plist, and forwards SIGTERM/SIGINT so launchctl stop works.
 */

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

static pid_t child_pid = 0;

static void forward_signal(int sig) {
    if (child_pid > 0)
        kill(child_pid, sig);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <script> [args...]\n", argv[0]);
        return 1;
    }

    child_pid = fork();
    if (child_pid == -1) {
        perror("fork");
        return 1;
    }

    if (child_pid == 0) {
        /* Child: exec bash with [script, args...] */
        char **new_argv = calloc(argc + 1, sizeof(char *));
        if (!new_argv) {
            perror("calloc");
            _exit(1);
        }
        new_argv[0] = "bash";
        for (int i = 1; i < argc; i++)
            new_argv[i] = argv[i];
        new_argv[argc] = NULL;

        execv("/bin/bash", new_argv);
        perror("execv");
        _exit(1);
    }

    /* Parent: forward signals and wait */
    signal(SIGTERM, forward_signal);
    signal(SIGINT, forward_signal);

    int status;
    waitpid(child_pid, &status, 0);
    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    return 1;
}
