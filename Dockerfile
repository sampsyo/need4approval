FROM alpine
MAINTAINER Adrian Sampson <adrian@radbox.org>

# Install Python, pip, and git.
RUN apk add --update py3-pip py3-cffi py3-cryptography git \
    && rm -rf /var/cache/apk/*

# Install poetry.
RUN pip3 install poetry

# Get the source code.
ADD .

# Install the project.
RUN poetry install

VOLUME /data
ENTRYPOINT ["poetry", "run", "n4a", "--dir", "/data"]
