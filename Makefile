# quarry — install helpers. Dependency-free; installs by symlinking bin/quarry
# onto your PATH and dropping shell-completion files into per-shell user dirs.
#
#   make install               symlink `quarry` into BINDIR
#   make install-completions   install fish/zsh/bash completions
#   make install-config        drop a starter config (won't clobber an existing one)
#   make test                  run the unit test suite
#   make uninstall             remove everything the above installed
#
# Override any directory on the command line, e.g.:
#   make install PREFIX=/usr/local

PREFIX   ?= $(HOME)/.local
BINDIR   ?= $(PREFIX)/bin

FISH_DIR ?= $(HOME)/.config/fish/completions
ZSH_DIR  ?= $(PREFIX)/share/zsh/site-functions
BASH_DIR ?= $(PREFIX)/share/bash-completion/completions

# Search config lives under XDG config home (honored by quarry itself too).
CONFIG_DIR ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/quarry

# Absolute path to the entry point, so the symlink works from any cwd. quarry-fzf
# is found as its sibling in bin/, so only `quarry` needs to be on PATH.
QUARRY  := $(abspath bin/quarry)
EXAMPLE := $(abspath config.ini.example)
PYTHON  ?= python3

.DEFAULT_GOAL := help

.PHONY: help install uninstall install-completions install-config test \
        install-fish install-zsh install-bash

help:
	@echo "quarry — make targets:"
	@echo "  install               symlink quarry into $(BINDIR)"
	@echo "  install-completions   install fish/zsh/bash completions"
	@echo "  install-config        drop a starter config (won't clobber existing)"
	@echo "  test                  run the unit test suite"
	@echo "  uninstall             remove the symlink and completion files"
	@echo ""
	@echo "Dirs (override on the command line): PREFIX=$(PREFIX)"

test:
	@$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

install:
	@mkdir -p "$(BINDIR)"
	@ln -sf "$(QUARRY)" "$(BINDIR)/quarry"
	@echo "installed: $(BINDIR)/quarry -> $(QUARRY)"
	@case ":$$PATH:" in *":$(BINDIR):"*) ;; \
	  *) echo "note: $(BINDIR) is not on your PATH — add it to use \`quarry\`." ;; esac
	@echo "next: make install-completions"

# fish autoloads this directory, so no rc edit is needed.
install-fish:
	@mkdir -p "$(FISH_DIR)"
	@"$(QUARRY)" completions fish > "$(FISH_DIR)/quarry.fish"
	@echo "fish: installed $(FISH_DIR)/quarry.fish (autoloads; open a new shell)"

# zsh needs the containing dir on $fpath before compinit runs.
install-zsh:
	@mkdir -p "$(ZSH_DIR)"
	@"$(QUARRY)" completions zsh > "$(ZSH_DIR)/_quarry"
	@echo "zsh: installed $(ZSH_DIR)/_quarry"
	@echo "     add to ~/.zshrc BEFORE 'compinit':"
	@echo "       fpath+=($(ZSH_DIR)); autoload -Uz compinit && compinit"

# bash-completion (v2) autoloads this XDG dir; otherwise source the file directly.
install-bash:
	@mkdir -p "$(BASH_DIR)"
	@"$(QUARRY)" completions bash > "$(BASH_DIR)/quarry"
	@echo "bash: installed $(BASH_DIR)/quarry"
	@echo "     if you don't use bash-completion, add to ~/.bashrc:"
	@echo "       source $(BASH_DIR)/quarry"

install-completions: install-fish install-zsh install-bash

# Starter config, only if the user has none — never overwrite their settings.
install-config:
	@mkdir -p "$(CONFIG_DIR)"
	@if [ -e "$(CONFIG_DIR)/config.ini" ]; then \
	  echo "config: $(CONFIG_DIR)/config.ini exists — left unchanged"; \
	else \
	  cp "$(EXAMPLE)" "$(CONFIG_DIR)/config.ini"; \
	  echo "config: installed $(CONFIG_DIR)/config.ini"; \
	fi

uninstall:
	@rm -f "$(BINDIR)/quarry" \
	       "$(FISH_DIR)/quarry.fish" \
	       "$(ZSH_DIR)/_quarry" \
	       "$(BASH_DIR)/quarry"
	@echo "removed quarry symlink and completion files"
