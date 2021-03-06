# Copyright (C) 2015 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from __future__ import print_function

import os
import os.path
import sys
import tempfile
import warnings
import yaml

from osc import cmdln
from osc import oscerr

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)
from osclib.accept_command import AcceptCommand
from osclib.adi_command import AdiCommand
from osclib.check_command import CheckCommand
from osclib.cleanup_rings import CleanupRings
from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.ignore_command import IgnoreCommand
from osclib.unignore_command import UnignoreCommand
from osclib.list_command import ListCommand
from osclib.obslock import OBSLock
from osclib.select_command import SelectCommand
from osclib.stagingapi import StagingAPI
from osclib.cache import Cache
from osclib.unselect_command import UnselectCommand
from osclib.repair_command import RepairCommand
from osclib.request_splitter import RequestSplitter

OSC_STAGING_VERSION = '0.0.1'


def _print_version(self):
    """ Print version information about this extension. """
    print(self.OSC_STAGING_VERSION)
    quit(0)


def _full_project_name(self, project):
    """Deduce the full project name."""
    if project.startswith(('openSUSE', 'SUSE')):
        return project

    if 'Factory' in project or 'openSUSE' in project:
        return 'openSUSE:%s' % project

    if 'SLE' in project:
        return 'SUSE:%s' % project

    # If we can't guess, raise a Warning
    warnings.warn('%s project not recognized.' % project)
    return project


@cmdln.option('--move', action='store_true',
              help='force the selection to become a move')
@cmdln.option('--by-develproject', action='store_true',
              help='sort the packages by devel project')
@cmdln.option('--split', action='store_true',
              help='splits each package to different adi staging')
@cmdln.option('--supersede', action='store_true',
              help='superseding requests. please make sure you have staging permissions')
@cmdln.option('-f', '--from', dest='from_', metavar='FROMPROJECT',
              help='manually specify different source project during request moving')
@cmdln.option('-p', '--project', dest='project', metavar='PROJECT', default='Factory',
              help='select a different project instead of openSUSE:Factory')
@cmdln.option('--add', dest='add', metavar='PACKAGE',
              help='mark additional packages to be checked by repo checker')
@cmdln.option('--force', action='store_true', 
              help='Force action, overruling internal checks (CAUTION)')
@cmdln.option('-o', '--old', action='store_true',
              help='use the old check algorithm')
@cmdln.option('-v', '--version', action='store_true',
              help='show version of the plugin')
@cmdln.option('--no-freeze', dest='no_freeze', action='store_true',
              help='force the select command ignoring the time from the last freeze')
@cmdln.option('--no-cleanup', dest='no_cleanup', action='store_true',
              help='do not cleanup remaining packages in staging projects after accept')
@cmdln.option('--no-bootstrap', dest='bootstrap', action='store_false', default=True,
              help='do not update bootstrap-copy when freezing')
@cmdln.option('--wipe-cache', dest='wipe_cache', action='store_true', default=False,
              help='wipe GET request cache before executing')
@cmdln.option('-m', '--message', help='message used by ignore command')
@cmdln.option('--filter-by', action='append', help='xpath by which to filter requests')
@cmdln.option('--group-by', action='append', help='xpath by which to group requests')
@cmdln.option('-i', '--interactive', action='store_true', help='interactively modify selection proposal')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    ${cmd_option_list}

    "accept" will accept all requests in
        openSUSE:Factory:Staging:<LETTER> (into Factory)

    "acheck" will check if it's safe to accept new staging projects
        As openSUSE:Factory is syncing the right package versions between
        /standard, /totest and /snapshot, it's important that the projects
        are clean prior to a checkin round.

    "check" will check if all packages are links without changes

    "cleanup_rings" will try to cleanup rings content and print
        out problems

    "freeze" will freeze the sources of the project's links (not
        affecting the packages actually in)

    "frozenage" will show when the respective staging project was last frozen

    "ignore" will ignore a request from "list" and "adi" commands until unignored

    "unignore" will remove from ignore list

    "list" will pick the requests not in rings

    "select" will add requests to the project
        Stagings are expected to be either in short-hand or the full project
        name. For example letter or named stagings can be specified simply as
        A, B, Gcc6, etc, while adi stagings can be specified as adi:1, adi:2,
        etc. Currently, adi stagings are not supported in proposal mode.

        Requests may either be the target package or the request ID.

        When using --filter-by or --group-by the xpath will be applied to the
        request node as returned by OBS. Several values will supplement the
        normal request node.

        - ./action/target/@devel_project: the devel project for the package
        - ./action/target/@ring: the ring to which the package belongs
        - ./@ignored: either false or the provided message

        Some useful examples:

        --filter-by './action/target[starts-with(@package, "yast-")]'
        --filter-by './action/target/[@devel_project="YaST:Head"]'
        --filter-by './action/target[starts-with(@ring, "1")]'
        --filter-by '@id!="1234567"'

        --group-by='./action/target/@devel_project'
        --group-by='./action/target/@ring'

        Multiple filter-by or group-by options may be used at the same time.

        Note that when using proposal mode, multiple stagings to consider may be
        provided in addition to a list of requests by which to filter. A more
        complex example:

        select --group-by='./action/target/@devel_project' A B C 123 456 789

        This will separate the requests 123, 456, 789 by devel project and only
        consider stagings A, B, or C, if available, for placement.

        No arguments is also a valid choice and will propose all non-ignored
        requests into the first available staging. Note that bootstrapped
        stagings are only used when either required or no other stagings are
        available.

        Another useful example is placing all open requests into a specific
        letter staging with:

        select A

        Interactive mode allows the proposal to be modified before application.

    "unselect" will remove from the project - pushing them back to the backlog

    Usage:
        osc staging accept [--force] [LETTER...]
        osc staging check [--old] REPO
        osc staging cleanup_rings
        osc staging freeze [--no-boostrap] PROJECT...
        osc staging frozenage PROJECT...
        osc staging ignore [-m MESSAGE] REQUEST...
        osc staging unignore REQUEST...|all
        osc staging list [--supersede]
        osc staging select [--no-freeze] [--move [--from PROJECT] STAGING REQUEST...
        osc staging select [--no-freeze] [[--interactive] [--filter-by...] [--group-by...]] [STAGING...] [REQUEST...]
        osc staging unselect REQUEST...
        osc staging repair REQUEST...
    """
    if opts.version:
        self._print_version()

    # verify the argument counts match the commands
    if len(args) == 0:
        raise oscerr.WrongArgs('No command given, see "osc help staging"!')
    cmd = args[0]
    if cmd in ('freeze', 'frozenage', 'repair'):
        min_args, max_args = 1, None
    elif cmd == 'check':
        min_args, max_args = 0, 2
    elif cmd == 'select':
        min_args, max_args = 0, None
    elif cmd == 'unselect':
        min_args, max_args = 1, None
    elif cmd == 'adi':
        min_args, max_args = None, None
    elif cmd in ('ignore', 'unignore'):
        min_args, max_args = 1, None
    elif cmd in ('list', 'accept'):
        min_args, max_args = 0, None
    elif cmd in ('cleanup_rings', 'acheck'):
        min_args, max_args = 0, 0
    else:
        raise oscerr.WrongArgs('Unknown command: %s' % cmd)
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if max_args is not None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # Init the OBS access and configuration
    opts.project = self._full_project_name(opts.project)
    opts.apiurl = self.get_api_url()
    opts.verbose = False
    Config(opts.project)

    if opts.wipe_cache:
        Cache.delete_all()

    with OBSLock(opts.apiurl, opts.project):
        api = StagingAPI(opts.apiurl, opts.project)

        # call the respective command and parse args by need
        if cmd == 'check':
            prj = args[1] if len(args) > 1 else None
            CheckCommand(api).perform(prj, opts.old)
        elif cmd == 'freeze':
            for prj in args[1:]:
                FreezeCommand(api).perform(api.prj_from_letter(prj), copy_bootstrap = opts.bootstrap )
        elif cmd == 'frozenage':
            for prj in args[1:]:
                print("%s last frozen %0.1f days ago" % (api.prj_from_letter(prj), api.days_since_last_freeze(api.prj_from_letter(prj))))
        elif cmd == 'acheck':
            # Is it safe to accept? Meaning: /totest contains what it should and is not dirty
            version_totest = api.get_binary_version(api.project, "openSUSE-release.rpm", repository="totest", arch="x86_64")
            if version_totest:
                version_openqa = api.load_file_content("%s:Staging" % api.project, "dashboard", "version_totest")
                totest_dirty = api.is_repo_dirty(api.project, 'totest')
                print("version_openqa: %s / version_totest: %s / totest_dirty: %s\n" % (version_openqa, version_totest, totest_dirty))
            else:
                print("acheck is unavailable in %s!\n" % (api.project))
        elif cmd == 'accept':
            # Is it safe to accept? Meaning: /totest contains what it should and is not dirty
            version_totest = api.get_binary_version(api.project, "openSUSE-release.rpm", repository="totest", arch="x86_64")

            if version_totest is None or opts.force:
                # SLE does not have a totest_version or openqa_version - ignore it
                version_openqa = version_totest
                totest_dirty   = False
            else:
                version_openqa = api.load_file_content("%s:Staging" % api.project, "dashboard", "version_totest")
                totest_dirty   = api.is_repo_dirty(api.project, 'totest')

            if version_openqa == version_totest and not totest_dirty:
                cmd = AcceptCommand(api)
                for prj in args[1:]:
                    if not cmd.perform(api.prj_from_letter(prj), opts.force):
                        return
                    if not opts.no_cleanup:
                        if api.item_exists(api.prj_from_letter(prj)):
                            cmd.cleanup(api.prj_from_letter(prj))
                        if api.item_exists("%s:DVD" % api.prj_from_letter(prj)):
                            cmd.cleanup("%s:DVD" % api.prj_from_letter(prj))
                if opts.project.startswith('openSUSE:'):
                    cmd.accept_other_new()
                    cmd.update_factory_version()
                    if api.item_exists(api.crebuild):
                        cmd.sync_buildfailures()
            else:
                print("Not safe to accept: /totest is not yet synced")
        elif cmd == 'unselect':
            UnselectCommand(api).perform(args[1:])
        elif cmd == 'select':
            # Include list of all stagings in short-hand and by full name.
            existing_stagings = api.get_staging_projects_short(None)
            existing_stagings += [p for p in api.get_staging_projects() if not p.endswith(':DVD')]
            stagings = []
            requests = []
            for arg in args[1:]:
                # Since requests may be given by either request ID or package
                # name and stagings may include multi-letter special stagings
                # there is no easy way to distinguish between stagings and
                # requests in arguments. Therefore, check if argument is in the
                # list of short-hand and full project name stagings, otherwise
                # consider it a request. This also allows for special stagings
                # with the same name as package, but the staging will be assumed
                # first time around. The current practice seems to be to start a
                # special staging with a capital letter which makes them unique.
                # lastly adi stagings are consistently prefix with adi: which
                # also makes it consistent to distinguish them from request IDs.
                if arg in existing_stagings and arg not in stagings:
                    stagings.append(api.extract_staging_short(arg))
                elif arg not in requests:
                    requests.append(arg)

            if len(stagings) != 1 or len(requests) == 0 or opts.filter_by or opts.group_by:
                if opts.move or opts.from_:
                    print('--move and --from must be used with explicit staging and request list')
                    return

                splitter = RequestSplitter(api, api.get_open_requests(), in_ring=True)
                if len(requests) > 0:
                    splitter.filter_add_requests(requests)
                if len(splitter.filters) == 0:
                    splitter.filter_add('./action[not(@type="add_role" or @type="change_devel")]')
                    splitter.filter_add('@ignored="false"')
                if opts.filter_by:
                    for filter_by in opts.filter_by:
                        splitter.filter_add(filter_by)
                if opts.group_by:
                    for group_by in opts.group_by:
                        splitter.group_by(group_by)
                splitter.split()

                result = splitter.propose_assignment(stagings)
                if result is not True:
                    print('Failed to generate proposal: {}'.format(result))
                    return
                proposal = splitter.proposal
                if len(proposal) == 0:
                    print('Empty proposal')
                    return

                if opts.interactive:
                    with tempfile.NamedTemporaryFile(suffix='.yml') as temp:
                        temp.write(yaml.safe_dump(splitter.proposal, default_flow_style=False) + '\n\n')
                        temp.write('# move requests between stagings or comment/remove them\n')
                        temp.write('# change the target staging for a group\n')
                        temp.write('# stagings\n')
                        temp.write('# - considered: {}\n'
                                   .format(', '.join(sorted(splitter.stagings_considerable.keys()))))
                        temp.write('# - remaining: {}\n'
                                   .format(', '.join(sorted(splitter.stagings_available.keys()))))
                        temp.flush()

                        editor = os.getenv('EDITOR')
                        if not editor:
                            editor = 'xdg-open'
                        return_code = subprocess.call([editor, temp.name])

                        proposal = yaml.safe_load(open(temp.name).read())

                print(yaml.safe_dump(proposal, default_flow_style=False))

                print('Accept proposal? [y/n] (y): ', end='')
                response = raw_input().lower()
                if response != '' and response != 'y':
                    print('Quit')
                    return

                for group in sorted(proposal.keys()):
                    g = proposal[group]
                    if not g['requests']:
                        # Skipping since all request removed, presumably in interactive.
                        continue

                    print('Staging {}'.format(g['staging']))

                    # SelectCommand expects strings.
                    request_ids = map(str, g['requests'].keys())
                    target_project = api.prj_from_short(g['staging'])

                    SelectCommand(api, target_project) \
                        .perform(request_ids, opts.move, opts.from_, opts.no_freeze)
            else:
                target_project = api.prj_from_short(stagings[0])
                if opts.add:
                    api.mark_additional_packages(target_project, [opts.add])
                else:
                    SelectCommand(api, target_project) \
                        .perform(requests, opts.move, opts.from_, opts.no_freeze)
        elif cmd == 'cleanup_rings':
            CleanupRings(api).perform()
        elif cmd == 'ignore':
            IgnoreCommand(api).perform(args[1:], opts.message)
        elif cmd == 'unignore':
            UnignoreCommand(api).perform(args[1:])
        elif cmd == 'list':
            ListCommand(api).perform(args[1:], supersede=opts.supersede)
        elif cmd == 'adi':
            AdiCommand(api).perform(args[1:], move=opts.move, by_dp=opts.by_develproject, split=opts.split)
        elif cmd == 'repair':
            RepairCommand(api).perform(args[1:])
