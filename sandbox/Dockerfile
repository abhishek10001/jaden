FROM mcr.microsoft.com/playwright:v1.48.0-noble

WORKDIR /workspace

RUN npm install -g vercel@latest playwright@1.48.0 && \
    git config --global user.email "agent@autonomous-sdlc.local" && \
    git config --global user.name "Autonomous SDLC Agent" && \
    git config --global init.defaultBranch main

ENV NODE_PATH="/usr/local/lib/node_modules:/usr/lib/node_modules"

CMD ["tail", "-f", "/dev/null"]
