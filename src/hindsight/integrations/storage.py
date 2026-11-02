"""Private bounded files. Component-by-component no-follow directory opens."""

import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path


def home_path(home=None):
    return Path(home).absolute() if home is not None else Path.home()


def safe_path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root) or ".." in path.parts:
        raise ValueError("Path outside permitted root")
    for current in reversed((path, *path.parents)):
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("Symlinks are not permitted")
            if current.is_relative_to(root) and info.st_uid != os.getuid():
                raise ValueError("Path not owned by current user")
    return path


@contextmanager
def parent_fd(path, root, create=False):
    """Hold directory descriptors to prevent ancestor replacement/symlink races."""
    path, root = safe_path(path, root), Path(root).absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    current = Path("/")
    try:
        for component in path.parent.parts[1:]:
            current /= component
            try:
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError:
                if not create or not current.is_relative_to(root):
                    raise
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if current.is_relative_to(root) and info.st_uid != os.getuid():
                raise ValueError("Directory not owned by current user")
        yield descriptor, path.name
    finally:
        os.close(descriptor)


def read_text(path, root, limit=1_048_576, private=False):
    with parent_fd(path, root) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise ValueError("Unsafe or oversized local file")
        if private and info.st_mode & 0o077:
            raise ValueError("Private file permissions must be 600")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise ValueError("Local file exceeds limit")
        return data.decode("utf-8")
    finally:
        os.close(descriptor)


def write_text(path, text, root):
    with parent_fd(path, root, create=True) as (parent, name):
        temporary = ".hindsight-" + secrets.token_hex(12)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def private_dir(home=None):
    root = home_path(home)
    directory = safe_path(root / ".config/hindsight", root)
    # Create using descriptors as well, never following a swapped ancestor.
    with parent_fd(directory / "placeholder", root, create=True) as (descriptor, _):
        if os.fstat(descriptor).st_mode & 0o077:
            raise ValueError("Hindsight private directory must be mode 700")
    return directory


def read_json(path, root, **kwargs):
    safe_path(path, root)
    try:
        return json.loads(read_text(path, root, **kwargs))
    except FileNotFoundError:
        return {}


def write_json(path, value, root):
    write_text(path, json.dumps(value, indent=2) + "\n", root)
