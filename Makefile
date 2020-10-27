PROGS = shell

include Makefile.include

CC += -fsanitize=address
LDLIBS += -lreadline

shell: shell.o command.o lexer.o jobs.o

test:

# vim: ts=8 sw=8 noet
