FROM gitpod/workspace-full

USER root
# Install custom tools, runtime, etc.
RUN apt-get update && apt-get install -y && apt-get install -y redis-server && apt-get clean && rm -rf /var/cache/apt/* && rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*
RUN curl https://cli-assets.heroku.com/install.sh | sh

USER gitpod

# Give back control
USER root

RUN service redis-server start 
