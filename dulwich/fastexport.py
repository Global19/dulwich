# __init__.py -- Fast export/import functionality
# Copyright (C) 2010 Jelmer Vernooij <jelmer@samba.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) any later version of
# the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.


"""Fast export/import functionality."""

from dulwich.index import (
    commit_tree,
    )
from dulwich.objects import (
    Blob,
    Commit,
    Tag,
    )
from fastimport import (
    commands,
    errors as fastimport_errors,
    parser,
    processor,
    )

import stat


def split_email(text):
    (name, email) = text.rsplit(" <", 1)
    return (name, email.rstrip(">"))


class GitFastExporter(object):
    """Generate a fast-export output stream for Git objects."""

    def __init__(self, outf, store):
        self.outf = outf
        self.store = store
        self.markers = {}
        self._marker_idx = 0

    def print_cmd(self, cmd):
        self.outf.write("%r\n" % cmd)

    def _allocate_marker(self):
        self._marker_idx+=1
        return str(self._marker_idx)

    def _export_blob(self, blob):
        marker = self._allocate_marker()
        self.markers[marker] = blob.id
        return (commands.BlobCommand(marker, blob.data), marker)

    def emit_blob(self, blob):
        (cmd, marker) = self._export_blob(blob)
        self.print_cmd(cmd)
        return marker

    def _iter_files(self, base_tree, new_tree):
        for (old_path, new_path), (old_mode, new_mode), (old_hexsha, new_hexsha) in \
                self.store.tree_changes(base_tree, new_tree):
            if new_path is None:
                yield commands.FileDeleteCommand(old_path)
                continue
            if not stat.S_ISDIR(new_mode):
                blob = self.store[new_hexsha]
                marker = self.emit_blob(blob)
            if old_path != new_path and old_path is not None:
                yield commands.FileRenameCommand(old_path, new_path)
            if old_mode != new_mode or old_hexsha != new_hexsha:
                yield commands.FileModifyCommand(new_path, new_mode, marker, None)

    def _export_commit(self, commit, ref, base_tree=None):
        file_cmds = list(self._iter_files(base_tree, commit.tree))
        marker = self._allocate_marker()
        if commit.parents:
            from_ = commit.parents[0]
            merges = commit.parents[1:]
        else:
            from_ = None
            merges = []
        author, author_email = split_email(commit.author)
        committer, committer_email = split_email(commit.committer)
        cmd = commands.CommitCommand(ref, marker,
            (author, author_email, commit.author_time, commit.author_timezone),
            (committer, committer_email, commit.commit_time, commit.commit_timezone),
            commit.message, from_, merges, file_cmds)
        return (cmd, marker)

    def emit_commit(self, commit, ref, base_tree=None):
        cmd, marker = self._export_commit(commit, ref, base_tree)
        self.print_cmd(cmd)
        return marker


class GitImportProcessor(processor.ImportProcessor):
    """An import processor that imports into a Git repository using Dulwich.

    """

    def __init__(self, repo, params=None, verbose=False, outf=None):
        processor.ImportProcessor.__init__(self, params, verbose)
        self.repo = repo
        self.last_commit = None

    def import_stream(self, stream):
        p = parser.ImportParser(stream)
        self.process(p.iter_commands)

    def blob_handler(self, cmd):
        """Process a BlobCommand."""
        self.repo.object_store.add_object(Blob.from_string(cmd.data))

    def checkpoint_handler(self, cmd):
        """Process a CheckpointCommand."""
        pass

    def commit_handler(self, cmd):
        """Process a CommitCommand."""
        commit = Commit()
        if cmd.author is not None:
            author = cmd.author
        else:
            author = cmd.committer
        (author_name, author_email, author_timestamp, author_timezone) = author
        (committer_name, committer_email, commit_timestamp, commit_timezone) = cmd.committer
        commit.author = "%s <%s>" % (author_name, author_email)
        commit.author_timezone = author_timezone
        commit.author_time = author_timestamp
        commit.committer = "%s <%s>" % (committer_name, committer_email)
        commit.commit_timezone = commit_timezone
        commit.commit_time = commit_timestamp
        commit.message = cmd.message
        commit.parents = []
        contents = {}
        commit.tree = commit_tree(self.repo.object_store,
            ((path, hexsha, mode) for (path, (mode, hexsha)) in
                contents.iteritems()))
        if self.last_commit is not None:
            commit.parents.append(self.last_commit)
        commit.parents += cmd.merges
        self.repo.object_store.add_object(commit)
        self.repo[cmd.ref] = commit.id
        self.last_commit = commit.id

    def progress_handler(self, cmd):
        """Process a ProgressCommand."""
        pass

    def reset_handler(self, cmd):
        """Process a ResetCommand."""
        self.last_commit = cmd.from_
        self.rep.refs[cmd.from_] = cmd.id

    def tag_handler(self, cmd):
        """Process a TagCommand."""
        tag = Tag()
        tag.tagger = cmd.tagger
        tag.message = cmd.message
        tag.name = cmd.tag
        self.repo.add_object(tag)
        self.repo.refs["refs/tags/" + tag.name] = tag.id

    def feature_handler(self, cmd):
        """Process a FeatureCommand."""
        raise fastimport_errors.UnknownFeature(cmd.feature_name)
