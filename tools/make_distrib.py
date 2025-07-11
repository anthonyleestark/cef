# Copyright (c) 2011 The Chromium Embedded Framework Authors. All rights
# reserved. Use of this source code is governed by a BSD-style license that
# can be found in the LICENSE file.

from __future__ import absolute_import
from __future__ import print_function
from bazel_util import bazel_substitute, bazel_last_error, bazel_set_quiet
from cef_version import VersionFormatter
from clang_util import clang_format_inplace
from date_util import *
from exec_util import exec_cmd
from file_util import *
import git_util as git
from io import open
from make_cmake import process_cmake_template
from optparse import OptionParser
import os
import re
import shlex
import subprocess
import sys
import tarfile
import zipfile


def create_zip_archive(input_dir):
  """ Creates a zip archive of the specified input directory. """
  zip_file = input_dir + '.zip'
  zf = zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED, True)

  def addDir(dir):
    for f in os.listdir(dir):
      full_path = os.path.join(dir, f)
      if os.path.isdir(full_path):
        addDir(full_path)
      else:
        zf.write(full_path, os.path.relpath(full_path, \
                 os.path.join(input_dir, os.pardir)))

  addDir(input_dir)
  zf.close()


def create_tar_archive(input_dir, format):
  """ Creates a tar archive of the specified input directory. """
  # Supported formats include "gz" and "bz2".
  tar_file = input_dir + '.tar.' + format
  tf = tarfile.open(tar_file, "w:" + format)
  # The default tar format changed from GNU_FORMAT to PAX_FORMAT in Python 3.8.
  # However, PAX_FORMAT generates additional @PaxHeader entries and truncates file
  # names on Windows, so we'll stick with the previous default.
  tf.format = tarfile.GNU_FORMAT
  tf.add(input_dir, arcname=os.path.basename(input_dir))
  tf.close()


def create_7z_archive(input_dir, format):
  """ Creates a 7z archive of the specified input directory. """
  # CEF_COMMAND_7ZIP might be "c:\Program Files (x86)\7Zip\7z.exe" or /usr/bin/7za
  # or simply 7z if the user knows that it's in the PATH var. Supported formats
  # depend on the 7za version -- check the 7-zip documentation for details.
  command = os.environ['CEF_COMMAND_7ZIP']
  working_dir = os.path.abspath(os.path.join(input_dir, os.pardir))

  tar_file = None
  if format in ('xz', 'gzip', 'bzip2'):
    # These formats only support one file per archive. Create a tar file first.
    tar_file = input_dir + '.tar'
    run('"%s" a -ttar -y %s %s' % (command, tar_file, input_dir), working_dir)
    zip_file = tar_file + '.' + format
    zip_input = tar_file
  else:
    zip_file = input_dir + '.' + format
    zip_input = input_dir

  # Create the compressed archive.
  run('"%s" a -t%s -y %s %s' % (command, format, zip_file, zip_input),
      working_dir)

  if not tar_file is None:
    remove_file(tar_file)


def create_output_dir(name, parent_dir):
  """ Creates an output directory and adds the path to the archive list. """
  output_dir = os.path.abspath(os.path.join(parent_dir, name))
  remove_dir(output_dir, options.quiet)
  make_dir(output_dir, options.quiet)
  archive_dirs.append(output_dir)
  return output_dir


def get_readme_component(name):
  """ Loads a README file component. """
  paths = []
  # platform directory
  if platform == 'windows':
    platform_cmp = 'win'
  elif platform == 'mac':
    platform_cmp = 'mac'
  elif platform == 'linux':
    platform_cmp = 'linux'
  paths.append(os.path.join(script_dir, 'distrib', platform_cmp))

  # shared directory
  paths.append(os.path.join(script_dir, 'distrib'))

  # load the file if it exists
  for path in paths:
    file = os.path.join(path, 'README.' + name + '.txt')
    if path_exists(file):
      return read_file(file)

  raise Exception('Readme component not found: ' + name)


def create_readme():
  """ Creates the README.TXT file. """
  # gather the components
  header_data = get_readme_component('header')
  mode_data = get_readme_component(mode)
  redistrib_data = get_readme_component('redistrib')
  footer_data = get_readme_component('footer')

  # format the file
  data = header_data + '\n\n' + mode_data
  if mode != 'sandbox' and mode != 'tools':
    data += '\n\n' + redistrib_data
  data += '\n\n' + footer_data
  data = data.replace('$CEF_URL$', cef_url)
  data = data.replace('$CEF_REV$', cef_rev)
  data = data.replace('$CEF_VER$', cef_ver)
  data = data.replace('$CHROMIUM_URL$', chromium_url)
  data = data.replace('$CHROMIUM_REV$', chromium_rev)
  data = data.replace('$CHROMIUM_VER$', chromium_ver)
  data = data.replace('$DATE$', date)

  if platform == 'windows':
    platform_str = 'Windows'
  elif platform == 'mac':
    platform_str = 'MacOS'
  elif platform == 'linux':
    platform_str = 'Linux'

  data = data.replace('$PLATFORM$', platform_str)

  if mode == 'standard':
    distrib_type = 'Standard'
    distrib_desc = 'This distribution contains all components necessary to build and distribute an\n' \
                   'application using CEF on the ' + platform_str + ' platform. Please see the LICENSING\n' \
                   'section of this document for licensing terms and conditions.'
  elif mode == 'minimal':
    distrib_type = 'Minimal'
    distrib_desc = 'This distribution contains the minimal components necessary to build and\n' \
                   'distribute an application using CEF on the ' + platform_str + ' platform. Please see\n' \
                   'the LICENSING section of this document for licensing terms and conditions.'
  elif mode == 'client':
    distrib_type = 'Client'
    if platform == 'linux':
      client_app = 'cefsimple'
    else:
      client_app = 'cefclient'
    distrib_desc = 'This distribution contains a release build of the ' + client_app + ' sample application\n' \
                   'for the ' + platform_str + ' platform. Please see the LICENSING section of this document for\n' \
                   'licensing terms and conditions.'
  elif mode == 'sandbox':
    distrib_type = 'Sandbox'
    if platform == 'windows':
      distrib_desc = 'This distribution contains only the bootstrap executables. Please see\n' \
                     'the LICENSING section of this document for licensing terms and conditions.'
    elif platform == 'mac':
      distrib_desc = 'This distribution contains only the cef_sandbox dynamic library. Please see\n' \
                     'the LICENSING section of this document for licensing terms and conditions.'
  elif mode == 'tools':
    distrib_type = 'Tools'
    distrib_desc = 'This distribution contains additional tools for building CEF-based applications.'

  data = data.replace('$DISTRIB_TYPE$', distrib_type)
  data = data.replace('$DISTRIB_DESC$', distrib_desc)

  write_file(os.path.join(output_dir, 'README.txt'), data)
  if not options.quiet:
    sys.stdout.write('Creating README.TXT file.\n')


def copy_gtest(tests_dir):
  """ Copy GTest files to the expected directory structure. """
  if not options.quiet:
    sys.stdout.write('Building gtest directory structure.\n')

  src_gtest_dir = os.path.join(cef_dir, 'tools', 'distrib', 'gtest')
  target_gtest_dir = os.path.join(tests_dir, 'gtest')

  # gtest header file at tests/gtest/include/gtest/gtest.h
  target_gtest_header_dir = os.path.join(target_gtest_dir, 'include', 'gtest')
  make_dir(target_gtest_header_dir, options.quiet)
  copy_file(
      os.path.join(src_gtest_dir, 'gtest.h'), target_gtest_header_dir,
      options.quiet)

  # gtest source file at tests/gtest/src/gtest-all.cc
  target_gtest_cpp_dir = os.path.join(target_gtest_dir, 'src')
  make_dir(target_gtest_cpp_dir, options.quiet)
  copy_file(
      os.path.join(src_gtest_dir, 'gtest-all.cc'), target_gtest_cpp_dir,
      options.quiet)

  # gtest LICENSE file at tests/gtest/LICENSE
  copy_file(
      os.path.join(src_gtest_dir, 'LICENSE'), target_gtest_dir, options.quiet)

  # CEF README file at tests/gtest/README.cef
  copy_file(
      os.path.join(src_gtest_dir, 'README.cef'),
      os.path.join(target_gtest_dir, 'README.cef'), options.quiet)

  # Copy tests/gtest/teamcity files
  copy_dir(
      os.path.join(cef_dir, 'tests', 'gtest', 'teamcity'),
      os.path.join(target_gtest_dir, 'teamcity'), options.quiet)


def transfer_doxyfile(dst_dir, quiet):
  """ Transfer and post-process the Doxyfile. """
  src_file = os.path.join(cef_dir, 'Doxyfile')
  if os.path.isfile(src_file):
    data = read_file(src_file)
    data = data.replace("$(PROJECT_NUMBER)", cef_ver)
    write_file(os.path.join(dst_dir, 'Doxyfile'), data)
    if not quiet:
      sys.stdout.write('Creating Doxyfile file.\n')


def transfer_gypi_files(src_dir,
                        gypi_paths,
                        gypi_path_prefix,
                        dst_dir,
                        quiet,
                        format=False):
  """ Transfer files from one location to another. """
  for path in gypi_paths:
    src = os.path.join(src_dir, path)
    dst = os.path.join(dst_dir, path.replace(gypi_path_prefix, ''))
    dst_path = os.path.dirname(dst)
    make_dir(dst_path, quiet)
    copy_file(src, dst, quiet)

    # Apply clang-format for C/C++ files.
    if format and os.path.splitext(dst)[1][1:] in ('c', 'cc', 'cpp', 'h'):
      print(dst)
      clang_format_inplace(dst)


def extract_toolchain_cmd(build_dir,
                          exe_name,
                          require_toolchain,
                          require_cmd=True):
  """ Extract a toolchain command from the ninja configuration file. """
  toolchain_ninja = os.path.join(build_dir, 'toolchain.ninja')
  if not os.path.isfile(toolchain_ninja):
    if not require_toolchain:
      return None, None
    raise Exception('Missing file: %s' % toolchain_ninja)

  data = read_file(toolchain_ninja)

  cmd = None
  path = None

  # Looking for a value like:
  #   command = python3 ../../v8/tools/run.py ./exe_name --arg1 --arg2
  # OR (for cross-compile):
  #   command = python3 ../../v8/tools/run.py ./clang_arch1_arch2/exe_name --arg1 --arg2
  findstr = '/%s ' % exe_name
  start = data.find(findstr)
  if start >= 0:
    # Extract the command-line arguments.
    after_start = start + len(findstr)
    end = data.find('\n', after_start)
    if end >= after_start:
      cmd = data[after_start:end].strip()
      print('%s command:' % exe_name, cmd)
      if cmd != '' and not re.match(r"^[0-9a-zA-Z\_\- ./=]{1,}$", cmd):
        cmd = None

    # Extract the relative file path.
    dot = start - 1
    while data[dot].isalnum() or data[dot] == '_':
      dot -= 1
    path = data[dot + 1:start]
    print('%s path:' % exe_name, path)
    if path != '' and not re.match(r"^(win_)?clang_[0-9a-z_]{1,}$", path):
      path = None

  if require_cmd and (cmd is None or path is None):
    raise Exception('Failed to extract %s command from %s' % (exe_name,
                                                              toolchain_ninja))

  return cmd, path


def get_exe_name(exe_name):
  return exe_name + ('.exe' if platform == 'windows' else '')


def get_script_name(script_name):
  return script_name + ('.bat' if platform == 'windows' else '.sh')


def transfer_tools_files(script_dir, build_dirs, output_dir):
  for build_dir in build_dirs:
    is_debug = build_dir.find('Debug') >= 0
    dst_dir_name = 'Debug' if is_debug else 'Release'
    dst_dir = os.path.join(output_dir, dst_dir_name)

    # Retrieve the binary path and command-line arguments.
    # See issue #3734 for the expected format.
    mksnapshot_name = 'mksnapshot'
    tool_cmd, tool_dir = extract_toolchain_cmd(
        build_dir, mksnapshot_name, require_toolchain=not options.allowpartial)
    if tool_cmd is None:
      sys.stdout.write("No %s build toolchain for %s.\n" % (dst_dir_name,
                                                            mksnapshot_name))
      continue

    if options.allowpartial and not path_exists(
        os.path.join(build_dir, tool_dir, get_exe_name(mksnapshot_name))):
      sys.stdout.write("No %s build of %s.\n" % (dst_dir_name, mksnapshot_name))
      continue

    # yapf: disable
    binaries = [
        {'path': get_exe_name(mksnapshot_name)},
        {'path': get_exe_name('v8_context_snapshot_generator')},
    ]
    # yapf: disable

    # Transfer binaries.
    copy_files_list(os.path.join(build_dir, tool_dir), dst_dir, binaries)

    # Evaluate command-line arguments and remove relative paths. Copy any input files
    # into the distribution.
    # - Example input path : ../../v8/tools/builtins-pgo/profiles/x64-rl.profile
    # - Example output path: gen/v8/embedded.S
    parsed_cmd = []
    for cmd in tool_cmd.split(' '):
      if cmd.find('/') > 0:
        file_name = os.path.split(cmd)[1]
        if len(file_name) == 0:
          raise Exception('Failed to parse %s command component: %s' % (mksnapshot_name, cmd))
        if cmd.startswith('../../'):
          file_path = os.path.realpath(os.path.join(build_dir, cmd))
          # Validate input file/path.
          if not file_path.startswith(src_dir):
            raise Exception('Invalid %s command input file: %s' % (mksnapshot_name, file_path))
          if not os.path.isfile(file_path):
            raise Exception('Missing %s command input file: %s' % (mksnapshot_name, file_path))
          # Transfer input file.
          copy_file(file_path, os.path.join(dst_dir, file_name), options.quiet)
        cmd = file_name
      parsed_cmd.append(cmd)

    # Write command-line arguments file.
    write_file(os.path.join(dst_dir, 'mksnapshot_cmd.txt'), ' '.join(parsed_cmd))

  # yapf: disable
  files = [
      {'path': get_script_name('run_mksnapshot')},
  ]
  # yapf: disable

  # Transfer other tools files.
  copy_files_list(os.path.join(script_dir, 'distrib', 'tools'), output_dir, files)


def copy_bazel_file_with_substitution(path, target_path, variables, relative_path):
  data = read_file(path)
  bazel_set_quiet(True)
  result = bazel_substitute(data, variables, path_relative_to=relative_path, label=path)
  last_error = bazel_last_error()
  bazel_set_quiet(False)
  if not last_error is None:
    raise Exception(last_error)
  if not options.quiet:
    sys.stdout.write('Writing %s file.\n' % target_path)
  write_file(target_path, result)


def transfer_bazel_files(bazel_dir, output_dir, variables, require_parent_dir):
  # All input files.
  bazel_files = get_files(os.path.join(bazel_dir, '*')) + get_files(os.path.join(bazel_dir, '.*'))

  # Map of path component to required platform.
  platform_map = {
    'linux': 'linux',
    'mac': 'mac',
    'win': 'windows',
  }

  for path in bazel_files:
    name = os.path.split(path)[1]

    # |name| uses hyphens to indicate directory components.
    directory_parts = name.split('-')[:-1]

    # Skip files that don't apply for the current platform.
    skip = False
    for part in directory_parts:
      if part in platform_map and platform_map[part] != platform:
        skip = True
        break
    if skip:
      sys.stdout.write('Skipping %s file.\n' % path)
      continue

    target_path = os.path.join(output_dir, name.replace('-', '/'))
    target_dir = os.path.split(target_path)[0]
    if not os.path.isdir(target_dir):
      parent_dir = os.path.split(target_dir)[0]
      if not os.path.isdir(parent_dir) and require_parent_dir:
        # Don't write tests/* files if the tests/ directory is missing.
        sys.stdout.write('Skipping %s file.\n' % path)
        continue
      make_dir(target_dir)
    if target_path.endswith('.in'):
      # Copy with variable substitution.
      relative_path = '/'.join(directory_parts)
      copy_bazel_file_with_substitution(path, target_path[:-3], variables, relative_path)
    else:
      # Copy as-is.
      copy_file(path, target_path, options.quiet)


def normalize_headers(file, new_path=''):
  """ Normalize headers post-processing. Remove the path component from any
      project include directives. """
  data = read_file(file)
  data = re.sub(r'''#include \"(?!include\/)[a-zA-Z0-9_\/]+\/+([a-zA-Z0-9_\.]+)\"''', \
                "// Include path modified for CEF Binary Distribution.\n#include \""+new_path+"\\1\"", data)
  write_file(file, data)


def eval_transfer_file(cef_dir, script_dir, transfer_cfg, output_dir, quiet):
  """ Transfer files based on the specified configuration. """
  if not path_exists(transfer_cfg):
    return

  configs = eval_file(transfer_cfg)
  for cfg in configs:
    dst = os.path.join(output_dir, cfg['target'])

    # perform a copy if source is specified
    if not cfg['source'] is None:
      src = os.path.join(cef_dir, cfg['source'])
      dst_path = os.path.dirname(dst)
      make_dir(dst_path, quiet)
      copy_file(src, dst, quiet)

      # place a readme file in the destination directory
      readme = os.path.join(dst_path, 'README-TRANSFER.txt')
      if not path_exists(readme):
        copy_file(
            os.path.join(script_dir, 'distrib/README-TRANSFER.txt'), readme)

      str = cfg['source'] + "\n"
      with open(readme, 'a', encoding='utf-8') as fp:
        if sys.version_info.major == 2:
          fp.write(str.decode('utf-8'))
        else:
          fp.write(str)

    # perform any required post-processing
    if 'post-process' in cfg:
      post = cfg['post-process']
      if post == 'normalize_headers':
        new_path = ''
        if 'new_header_path' in cfg:
          new_path = cfg['new_header_path']
        normalize_headers(dst, new_path)


def transfer_files(cef_dir, script_dir, transfer_cfg_dir, mode, output_dir,
                   quiet):
  # Non-mode-specific transfers.
  transfer_cfg = os.path.join(transfer_cfg_dir, 'transfer.cfg')
  eval_transfer_file(cef_dir, script_dir, transfer_cfg, output_dir, quiet)
  # Mode-specific transfers.
  transfer_cfg = os.path.join(transfer_cfg_dir, 'transfer_%s.cfg' % mode)
  eval_transfer_file(cef_dir, script_dir, transfer_cfg, output_dir, quiet)


# |paths| is a list of dictionary values with the following keys:
# path        [required]  Input file or directory path relative to |build_dir|.
#                         By default this will also be the output path relative
#                         to |dst_dir|.
# out_path    [optional]  Override the output path relative to |dst_dir|.
# conditional [optional]  Set to True if the path is conditional on build
#                         settings. Missing conditional paths will not be
#                         treated as an error.
# delete      [optional]  Glob pattern of files to delete after the copy.
def copy_files_list(build_dir, dst_dir, paths):
  ''' Copy the files listed in |paths| from |build_dir| to |dst_dir|. '''
  for entry in paths:
    source_path = os.path.join(build_dir, entry['path'])
    if os.path.exists(source_path):
      target_path = os.path.join(dst_dir, entry['out_path']
                                 if 'out_path' in entry else entry['path'])
      make_dir(os.path.dirname(target_path), options.quiet)
      if os.path.isdir(source_path):
        copy_dir(source_path, target_path, options.quiet)
        if 'delete' in entry:
          for delete_path in get_files(
              os.path.join(target_path, entry['delete'])):
            if not os.path.isdir(delete_path):
              remove_file(delete_path, options.quiet)
            else:
              raise Exception('Refusing to delete directory: %s' % delete_path)
      else:
        copy_file(source_path, target_path, options.quiet)
    else:
      if 'conditional' in entry and entry['conditional']:
        sys.stdout.write('Missing conditional path: %s.\n' % source_path)
      else:
        raise Exception('Missing required path: %s' % source_path)


def run(command_line, working_dir):
  """ Run a command. """
  sys.stdout.write('-------- Running "'+command_line+'" in "'+\
                   working_dir+'"...'+"\n")
  args = shlex.split(command_line.replace('\\', '\\\\'))
  return subprocess.check_call(
      args, cwd=working_dir, env=os.environ, shell=(sys.platform == 'win32'))


def print_error(msg):
  print('Error: %s\nSee --help for usage.' % msg)


# cannot be loaded as a module
if __name__ != "__main__":
  sys.stderr.write('This file cannot be loaded as a module!')
  sys.exit()

# parse command-line options
disc = """
This utility builds the CEF Binary Distribution.
"""

parser = OptionParser(description=disc)
parser.add_option(
    '--output-dir',
    dest='outputdir',
    metavar='DIR',
    help='output directory [required]')
parser.add_option(
    '--distrib-subdir',
    dest='distribsubdir',
    help='name of the subdirectory for the distribution',
    default='')
parser.add_option(
    '--distrib-subdir-suffix',
    dest='distribsubdirsuffix',
    help='suffix added to name of the subdirectory for the distribution',
    default='')
parser.add_option(
    '--allow-partial',
    action='store_true',
    dest='allowpartial',
    default=False,
    help='allow creation of partial distributions')
parser.add_option(
    '--no-symbols',
    action='store_true',
    dest='nosymbols',
    default=False,
    help='don\'t create symbol files')
parser.add_option(
    '--symbols-only',
    action='store_true',
    dest='symbolsonly',
    default=False,
    help='only create symbol files')
parser.add_option(
    '--debug-symbols-only',
    action='store_true',
    dest='debugsymbolsonly',
    default=False,
    help='only create debug symbol files')
parser.add_option(
    '--release-symbols-only',
    action='store_true',
    dest='releasesymbolsonly',
    default=False,
    help='only create release symbol files')
parser.add_option(
    '--no-docs',
    action='store_true',
    dest='nodocs',
    default=False,
    help='don\'t create documentation')
parser.add_option(
    '--no-archive',
    action='store_true',
    dest='noarchive',
    default=False,
    help='don\'t create archives for output directories')
parser.add_option(
    '--no-sandbox',
    action='store_true',
    dest='nosandbox',
    default=False,
    help='don\'t create cef_sandbox files')
parser.add_option(
    '--no-format',
    action='store_true',
    dest='noformat',
    default=False,
    help='don\'t format autogenerated C/C++ files')
parser.add_option(
    '--ninja-build',
    action='store_true',
    dest='ninjabuild',
    default=False,
    help='build was created using ninja')
parser.add_option(
    '--x64-build',
    action='store_true',
    dest='x64build',
    default=False,
    help='create a 64-bit binary distribution')
parser.add_option(
    '--arm-build',
    action='store_true',
    dest='armbuild',
    default=False,
    help='create an ARM binary distribution (Linux only)')
parser.add_option(
    '--arm64-build',
    action='store_true',
    dest='arm64build',
    default=False,
    help='create an ARM64 binary distribution (Linux only)')
parser.add_option(
    '--minimal',
    action='store_true',
    dest='minimal',
    default=False,
    help='include only release build binary files')
parser.add_option(
    '--client',
    action='store_true',
    dest='client',
    default=False,
    help='include only the sample application')
parser.add_option(
    '--sandbox',
    action='store_true',
    dest='sandbox',
    default=False,
    help='include only the cef_sandbox static library (macOS) or bootstrap executables (Windows)')
parser.add_option(
    '--tools',
    action='store_true',
    dest='tools',
    default=False,
    help='include only the tools')
parser.add_option(
    '--ozone',
    action='store_true',
    dest='ozone',
    default=False,
    help='include ozone build related files (Linux only)')
parser.add_option(
    '-q',
    '--quiet',
    action='store_true',
    dest='quiet',
    default=False,
    help='do not output detailed status information')
(options, args) = parser.parse_args()

# Test the operating system.
platform = ''
if sys.platform == 'win32':
  platform = 'windows'
elif sys.platform == 'darwin':
  platform = 'mac'
elif sys.platform.startswith('linux'):
  platform = 'linux'

# the outputdir option is required
if options.outputdir is None:
  print_error('--output-dir is required.')
  sys.exit()

if options.minimal and options.client:
  print_error('Cannot specify both --minimal and --client.')
  sys.exit()

if options.x64build + options.armbuild + options.arm64build > 1:
  print_error('Invalid combination of build options.')
  sys.exit()

if options.armbuild and platform != 'linux':
  print_error('--arm-build is only supported on Linux.')
  sys.exit()

if options.sandbox and not platform in ('mac', 'windows'):
  print_error('--sandbox is only supported on macOS and Windows.')
  sys.exit()

if not options.ninjabuild:
  print_error('--ninja-build is required.')
  sys.exit()

if options.ozone and platform != 'linux':
  print_error('--ozone is only supported on Linux.')
  sys.exit()

symbols_only_options = (options.symbolsonly, options.debugsymbolsonly, options.releasesymbolsonly)
if (options.nosymbols and any(symbols_only_options)) or sum(symbols_only_options) > 1:
  print_error('Invalid combination of build options.')
  sys.exit()

# script directory
script_dir = os.path.dirname(__file__)

# CEF root directory
cef_dir = os.path.realpath(os.path.join(script_dir, os.pardir))

# src directory
src_dir = os.path.realpath(os.path.join(cef_dir, os.pardir))

if not git.is_checkout(cef_dir):
  raise Exception('Not a valid checkout: %s' % (cef_dir))

# retrieve information for CEF
cef_url = git.get_url(cef_dir)
cef_rev = git.get_hash(cef_dir)
cef_commit_number = git.get_commit_number(cef_dir)

# retrieve information for Chromium
if git.is_checkout(src_dir):
  chromium_url = git.get_url(src_dir)
  chromium_rev = git.get_hash(src_dir)
else:
  # Using a source tarball. Assume the default URL.
  chromium_url = 'https://chromium.googlesource.com/chromium/src.git'

  # Extract <hash> from a value like:
  # LASTCHANGE=<hash>-refs/branch-heads/<branch>@{#<count>}
  chromium_rev = None
  lastchange_path = os.path.join(src_dir, 'build', 'util', 'LASTCHANGE')
  with open(lastchange_path, 'r') as infile:
    for line in infile:
      key, val = line.strip().split('=', 2)
      if key == 'LASTCHANGE':
        chromium_rev = val.split('-')[0]
        break
  if chromium_rev is None:
    raise Exception('Failed to read Chromium hash from %s' % lastchange_path)

date = get_date()

# format version strings
version_formatter = VersionFormatter()
cef_ver = version_formatter.get_version_string()
chromium_ver = version_formatter.get_chromium_version_string()

# list of output directories to be archived
archive_dirs = []

if options.x64build:
  platform_arch = '64'
  binary_arch = 'x64'
elif options.armbuild:
  platform_arch = 'arm'
  binary_arch = 'arm'
elif options.arm64build:
  platform_arch = 'arm64'
  binary_arch = 'arm64'
else:
  platform_arch = '32'
  binary_arch = 'x86'

# output directory
output_dir_base = 'cef_binary_' + cef_ver

if options.distribsubdir == '':
  if platform == 'mac':
    # For backwards compatibility keep the old default directory name on mac.
    platform_name = 'macos' + ('x' if platform_arch == '64' else '')
  else:
    platform_name = platform

  output_dir_name = output_dir_base + '_' + platform_name + platform_arch
  if options.distribsubdirsuffix != '':
    output_dir_name += '_' + options.distribsubdirsuffix
else:
  output_dir_name = options.distribsubdir

if options.minimal:
  mode = 'minimal'
  output_dir_name = output_dir_name + '_minimal'
elif options.client:
  mode = 'client'
  output_dir_name = output_dir_name + '_client'
elif options.sandbox:
  mode = 'sandbox'
  output_dir_name = output_dir_name + '_sandbox'
elif options.tools:
  mode = 'tools'
  output_dir_name = output_dir_name + '_tools'
elif any(symbols_only_options):
  if options.debugsymbolsonly:
    mode = 'debug-symbols'
  elif options.releasesymbolsonly:
    mode = 'release-symbols'
  else:
    mode = 'symbols'
else:
  mode = 'standard'

if options.ozone:
  output_dir_name = output_dir_name + '_ozone'

if not mode.endswith('symbols'):
  output_dir = create_output_dir(output_dir_name, options.outputdir)

  # create the README.TXT file
  create_readme()

  # transfer the LICENSE.txt file
  copy_file(os.path.join(cef_dir, 'LICENSE.txt'), output_dir, options.quiet)

# read the variables list from the autogenerated cef_paths.gypi file
cef_paths = eval_file(os.path.join(cef_dir, 'cef_paths.gypi'))
cef_paths = cef_paths['variables']

# read the variables list from the manually edited cef_paths2.gypi file
cef_paths2 = eval_file(os.path.join(cef_dir, 'cef_paths2.gypi'))
cef_paths2 = cef_paths2['variables']

# Determine the build directory suffix. CEF uses a consistent directory naming
# scheme for GN via GetAllPlatformConfigs in gn_args.py.
if options.x64build:
  build_dir_suffix = '_GN_x64'
elif options.armbuild:
  build_dir_suffix = '_GN_arm'
elif options.arm64build:
  build_dir_suffix = '_GN_arm64'
else:
  build_dir_suffix = '_GN_x86'

# Determine the build directory paths.
out_dir = os.path.join(src_dir, 'out')
build_dir_debug = os.path.join(out_dir, 'Debug' + build_dir_suffix)
build_dir_release = os.path.join(out_dir, 'Release' + build_dir_suffix)

if not mode.endswith('symbols'):
  # Transfer the about_credits.html file.
  # Debug and Release build should be the same so grab whichever exists.
  rel_path = os.path.join('gen', 'components', 'resources', 'about_credits.html')
  src_path = os.path.join(build_dir_release, rel_path)
  if not os.path.exists(src_path):
    src_path = os.path.join(build_dir_debug, rel_path)
    if not os.path.exists(src_path):
      raise Exception('Missing generated resources file: %s' % rel_path)
  copy_file(src_path, os.path.join(output_dir, 'CREDITS.html'), options.quiet)

if mode == 'standard' or mode == 'minimal':
  # create the include directory
  include_dir = os.path.join(output_dir, 'include')
  make_dir(include_dir, options.quiet)

  # create the cmake directory
  cmake_dir = os.path.join(output_dir, 'cmake')
  make_dir(cmake_dir, options.quiet)

  # create the libcef_dll_wrapper directory
  libcef_dll_dir = os.path.join(output_dir, 'libcef_dll')
  make_dir(libcef_dll_dir, options.quiet)

  # transfer common include files
  transfer_gypi_files(cef_dir, cef_paths2['includes_common'],
                      'include/', include_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['includes_common_capi'],
                      'include/', include_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['includes_capi'],
                      'include/', include_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['includes_wrapper'],
                      'include/', include_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths['autogen_cpp_includes'],
                      'include/', include_dir, options.quiet, format=not options.noformat)
  transfer_gypi_files(cef_dir, cef_paths['autogen_capi_includes'],
                      'include/', include_dir, options.quiet, format=not options.noformat)

  # Transfer generated include files.
  generated_includes = [
      'cef_api_versions.h',
      'cef_color_ids.h',
      'cef_command_ids.h',
      'cef_config.h',
      'cef_pack_resources.h',
      'cef_pack_strings.h',
      'cef_version.h',
  ]
  for include in generated_includes:
    # Debug and Release build should be the same so grab whichever exists.
    rel_path = os.path.join('gen', 'cef', 'include', include)
    src_path = os.path.join(build_dir_release, rel_path)
    if not os.path.exists(src_path):
      src_path = os.path.join(build_dir_debug, rel_path)
      if not os.path.exists(src_path):
        raise Exception('Missing generated header file: %s' % include)
    copy_file(src_path, os.path.join(include_dir, include), options.quiet)

  # transfer common libcef_dll_wrapper files
  transfer_gypi_files(cef_dir, cef_paths2['libcef_dll_wrapper_sources_base'],
                      'libcef_dll/', libcef_dll_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['libcef_dll_wrapper_sources_common'],
                      'libcef_dll/', libcef_dll_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths['autogen_client_side'],
                      'libcef_dll/', libcef_dll_dir, options.quiet, format=not options.noformat)

  if mode == 'standard' or mode == 'minimal':
    # transfer additional files
    transfer_files(cef_dir, script_dir, os.path.join(script_dir, 'distrib'), \
                   mode, output_dir, options.quiet)

  # process cmake templates
  variables = cef_paths.copy()
  variables.update(cef_paths2)
  process_cmake_template(os.path.join(cef_dir, 'CMakeLists.txt.in'), \
                         os.path.join(output_dir, 'CMakeLists.txt'), \
                         variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'cmake', 'cef_macros.cmake.in'), \
                         os.path.join(cmake_dir, 'cef_macros.cmake'), \
                         variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'cmake', 'cef_variables.cmake.in'), \
                         os.path.join(cmake_dir, 'cef_variables.cmake'), \
                         variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'cmake', 'FindCEF.cmake.in'), \
                         os.path.join(cmake_dir, 'FindCEF.cmake'), \
                         variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'libcef_dll', 'CMakeLists.txt.in'), \
                         os.path.join(libcef_dll_dir, 'CMakeLists.txt'), \
                         variables, options.quiet)

if mode == 'standard':
  # create the tests directory
  tests_dir = os.path.join(output_dir, 'tests')
  make_dir(tests_dir, options.quiet)

  # create the tests/shared directory
  shared_dir = os.path.join(tests_dir, 'shared')
  make_dir(shared_dir, options.quiet)

  if not options.ozone:
    # create the tests/cefclient directory
    cefclient_dir = os.path.join(tests_dir, 'cefclient')
    make_dir(cefclient_dir, options.quiet)

  # create the tests/cefsimple directory
  cefsimple_dir = os.path.join(tests_dir, 'cefsimple')
  make_dir(cefsimple_dir, options.quiet)

  # create the tests/ceftests directory
  ceftests_dir = os.path.join(tests_dir, 'ceftests')
  make_dir(ceftests_dir, options.quiet)

  # transfer common shared files
  transfer_gypi_files(cef_dir, cef_paths2['shared_sources_browser'], \
                      'tests/shared/', shared_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['shared_sources_common'], \
                      'tests/shared/', shared_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['shared_sources_renderer'], \
                      'tests/shared/', shared_dir, options.quiet)
  transfer_gypi_files(cef_dir, cef_paths2['shared_sources_resources'], \
                      'tests/shared/', shared_dir, options.quiet)

  if not options.ozone:
    # transfer common cefclient files
    transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_browser'], \
                        'tests/cefclient/', cefclient_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_common'], \
                        'tests/cefclient/', cefclient_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_renderer'], \
                        'tests/cefclient/', cefclient_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_resources'], \
                        'tests/cefclient/', cefclient_dir, options.quiet)

  # transfer common cefsimple files
  transfer_gypi_files(cef_dir, cef_paths2['cefsimple_sources_common'], \
                      'tests/cefsimple/', cefsimple_dir, options.quiet)

  # transfer common ceftests files
  transfer_gypi_files(cef_dir, cef_paths2['ceftests_sources_common'], \
                      'tests/ceftests/', ceftests_dir, options.quiet)

  # copy GTest files
  copy_gtest(tests_dir)

  # process cmake templates
  if not options.ozone:
    process_cmake_template(os.path.join(cef_dir, 'tests', 'cefclient', 'CMakeLists.txt.in'), \
                           os.path.join(cefclient_dir, 'CMakeLists.txt'), \
                           variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'tests', 'cefsimple', 'CMakeLists.txt.in'), \
                         os.path.join(cefsimple_dir, 'CMakeLists.txt'), \
                         variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'tests', 'gtest', 'CMakeLists.txt.in'), \
                         os.path.join(tests_dir, 'gtest', 'CMakeLists.txt'), \
                         variables, options.quiet)
  process_cmake_template(os.path.join(cef_dir, 'tests', 'ceftests', 'CMakeLists.txt.in'), \
                         os.path.join(ceftests_dir, 'CMakeLists.txt'), \
                         variables, options.quiet)

  # transfer gypi files
  copy_file(os.path.join(cef_dir, 'cef_paths.gypi'), \
            os.path.join(output_dir, 'cef_paths.gypi'), options.quiet)
  copy_file(os.path.join(cef_dir, 'cef_paths2.gypi'), \
            os.path.join(output_dir, 'cef_paths2.gypi'), options.quiet)

  # transfer Doxyfile
  transfer_doxyfile(output_dir, options.quiet)

  # transfer README.md
  copy_file(os.path.join(cef_dir, 'README.md'), \
            os.path.join(output_dir, 'README.md'), options.quiet)

if not options.nodocs:
  # generate doc files
  sys.stdout.write("Generating docs...\n")
  result = exec_cmd(
      os.path.join('tools', 'make_cppdocs.%s' %
                   ('bat' if platform == 'windows' else 'sh')), cef_dir)
  if (len(result['err']) > 0):
    sys.stdout.write(result['err'])
  sys.stdout.write(result['out'])

  src_dir = os.path.join(cef_dir, 'docs')
  if path_exists(src_dir):
    # create the docs output directory
    docs_output_dir = create_output_dir(output_dir_base + '_docs',
                                        options.outputdir)
    # transfer contents
    copy_dir(src_dir, docs_output_dir, options.quiet)
  else:
    sys.stdout.write("ERROR: No docs generated.\n")

if mode == 'tools':
  transfer_tools_files(script_dir, (build_dir_debug, build_dir_release),
                       output_dir)
elif platform == 'windows':
  libcef_dll = 'libcef.dll'
  # yapf: disable
  binaries = [
      {'path': 'chrome_elf.dll'},
      {'path': 'd3dcompiler_47.dll'},
      {'path': 'dxcompiler.dll', 'conditional': True},
      {'path': 'dxil.dll', 'conditional': True},
      {'path': libcef_dll},
      {'path': 'libEGL.dll'},
      {'path': 'libGLESv2.dll'},
      {'path': 'v8_context_snapshot.bin'},
      {'path': 'vk_swiftshader.dll'},
      {'path': 'vk_swiftshader_icd.json'},
      {'path': 'vulkan-1.dll'},
  ]
  pdb_files = [
      {'path': 'chrome_elf.dll.pdb'},
      {'path': 'dxcompiler.dll.pdb', 'conditional': True},
      {'path': '%s.pdb' % libcef_dll},
      {'path': 'libEGL.dll.pdb'},
      {'path': 'libGLESv2.dll.pdb'},
      {'path': 'vk_swiftshader.dll.pdb'},
      {'path': 'vulkan-1.dll.pdb'},
  ]
  # yapf: enable

  if mode == 'client':
    binaries.append({
        'path': 'cefsimple.exe' if platform_arch == 'arm64' else 'cefclient.exe'
    })
  else:
    if mode == 'sandbox':
      # Only include the sandbox binaries.
      binaries = []
      pdb_files = []

    # yapf: disable
    binaries.extend([
        {'path': 'bootstrap.exe'},
        {'path': 'bootstrapc.exe'},
    ])
    pdb_files.extend([
        {'path': 'bootstrap.exe.pdb'},
        {'path': 'bootstrapc.exe.pdb'},
    ])
    # yapf: enable

    if mode != 'sandbox':
      binaries.append({'path': '%s.lib' % libcef_dll, 'out_path': 'libcef.lib'})

  # yapf: disable
  resources = [
      {'path': 'chrome_100_percent.pak'},
      {'path': 'chrome_200_percent.pak'},
      {'path': 'resources.pak'},
      {'path': 'icudtl.dat'},
      {'path': 'locales', 'delete': '*.info'},
  ]
  # yapf: enable

  valid_build_dir = None

  if mode == 'standard' or mode == 'sandbox' or mode.endswith('symbols'):
    # transfer Debug files
    build_dir = build_dir_debug
    if not options.allowpartial or path_exists(
        os.path.join(build_dir, libcef_dll)):
      if not mode.endswith('symbols'):
        valid_build_dir = build_dir
        dst_dir = os.path.join(output_dir, 'Debug')
        copy_files_list(build_dir, dst_dir, binaries)

      if not options.nosymbols and mode != 'release-symbols':
        # create the symbol output directory
        symbol_output_dir = create_output_dir(
            output_dir_name + '_debug_symbols', options.outputdir)
        # transfer contents
        copy_files_list(build_dir, symbol_output_dir, pdb_files)
    else:
      sys.stdout.write("No Debug build files.\n")

  # transfer Release files
  build_dir = build_dir_release
  if not options.allowpartial or path_exists(
      os.path.join(build_dir, libcef_dll)):
    if not mode.endswith('symbols'):
      valid_build_dir = build_dir
      dst_dir = os.path.join(output_dir, 'Release')
      copy_files_list(build_dir, dst_dir, binaries)

    if not options.nosymbols and mode != 'debug-symbols':
      # create the symbol output directory
      symbol_output_dir = create_output_dir(
          output_dir_name + '_release_symbols', options.outputdir)
      # transfer contents
      copy_files_list(build_dir, symbol_output_dir, pdb_files)
  else:
    sys.stdout.write("No Release build files.\n")

  if mode != 'sandbox' and not valid_build_dir is None:
    # transfer resource files
    build_dir = valid_build_dir
    if mode == 'client':
      dst_dir = os.path.join(output_dir, 'Release')
    else:
      dst_dir = os.path.join(output_dir, 'Resources')
    copy_files_list(build_dir, dst_dir, resources)

  if mode == 'standard' or mode == 'minimal':
    # transfer include files
    transfer_gypi_files(cef_dir, cef_paths2['includes_win'], \
                        'include/', include_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['includes_win_capi'], \
                        'include/', include_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['includes_wrapper_win'], \
                        'include/', include_dir, options.quiet)

    # transfer libcef_dll_wrapper files
    transfer_gypi_files(cef_dir, cef_paths2['libcef_dll_wrapper_sources_win'], \
                        'libcef_dll/', libcef_dll_dir, options.quiet)

    # transfer additional files, if any
    transfer_files(cef_dir, script_dir, os.path.join(script_dir, 'distrib', 'win'), \
                   mode, output_dir, options.quiet)

  if mode == 'standard':
    # transfer shared files
    transfer_gypi_files(cef_dir, cef_paths2['shared_sources_win'], \
                        'tests/shared/', shared_dir, options.quiet)

    # transfer cefclient files
    transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_win'] +
                        cef_paths2['cefclient_sources_resources_win'] +
                        cef_paths2['cefclient_sources_resources_win_rc'],
                        'tests/cefclient/', cefclient_dir, options.quiet)

    # transfer cefsimple files
    transfer_gypi_files(cef_dir, cef_paths2['cefsimple_sources_win'] +
                        cef_paths2['cefsimple_sources_resources_win'] +
                        cef_paths2['cefsimple_sources_resources_win_rc'],
                        'tests/cefsimple/', cefsimple_dir, options.quiet)

    # transfer ceftests files
    transfer_gypi_files(cef_dir, cef_paths2['ceftests_sources_win'] +
                        cef_paths2['ceftests_sources_resources_win'] +
                        cef_paths2['ceftests_sources_resources_win_rc'],
                        'tests/ceftests/', ceftests_dir, options.quiet)

elif platform == 'mac':
  framework_name = 'Chromium Embedded Framework'
  cefclient_app = 'cefclient.app'

  dsym_dirs = [
      '%s.dSYM' % framework_name,
      'libEGL.dylib.dSYM',
      'libGLESv2.dylib.dSYM',
      'libvk_swiftshader.dylib.dSYM',
  ]

  sandbox_lib = 'libcef_sandbox.dylib'
  if mode == 'sandbox':
    # Only transfer the sandbox dSYM.
    dsym_dirs = []
  dsym_dirs.append('%s.dSYM' % sandbox_lib)

  valid_build_dir = None

  if mode == 'standard' or mode == 'sandbox' or mode.endswith('symbols'):
    # transfer Debug files
    build_dir = build_dir_debug
    if not options.allowpartial or path_exists(
        os.path.join(build_dir, cefclient_app)):
      if not mode.endswith('symbols'):
        valid_build_dir = build_dir
        dst_dir = os.path.join(output_dir, 'Debug')
        make_dir(dst_dir, options.quiet)
        framework_src_dir = os.path.join(
            build_dir, '%s/Contents/Frameworks/%s.framework/Versions/A' %
            (cefclient_app, framework_name))

        if mode == 'sandbox':
          # Only transfer the sandbox library.
          copy_file(
              os.path.join(framework_src_dir, 'Libraries', sandbox_lib),
              dst_dir, options.quiet)
        else:
          framework_dst_dir = os.path.join(dst_dir,
                                           '%s.framework' % framework_name)
          copy_dir(framework_src_dir, framework_dst_dir, options.quiet)

      if not options.nosymbols and mode != 'release-symbols':
        # create the symbol output directory
        symbol_output_dir = create_output_dir(
            output_dir_name + '_debug_symbols', options.outputdir)

        # The real dSYM already exists, just copy it to the output directory.
        # dSYMs are only generated when is_official_build=true or enable_dsyms=true.
        # See //build/config/mac/symbols.gni.
        for dsym in dsym_dirs:
          copy_dir(
              os.path.join(build_dir, dsym),
              os.path.join(symbol_output_dir, dsym), options.quiet)
    else:
      sys.stdout.write("No Debug build files.\n")

  # transfer Release files
  build_dir = build_dir_release
  if not options.allowpartial or path_exists(
      os.path.join(build_dir, cefclient_app)):
    if not mode.endswith('symbols'):
      valid_build_dir = build_dir
      dst_dir = os.path.join(output_dir, 'Release')
      make_dir(dst_dir, options.quiet)
      framework_src_dir = os.path.join(
          build_dir, '%s/Contents/Frameworks/%s.framework/Versions/A' %
          (cefclient_app, framework_name))

      if mode == 'sandbox':
        # Only transfer the sandbox library.
        copy_file(
            os.path.join(framework_src_dir, 'Libraries', sandbox_lib), dst_dir,
            options.quiet)
      else:
        if mode != 'client':
          framework_dst_dir = os.path.join(dst_dir,
                                           '%s.framework' % framework_name)
        else:
          copy_dir(
              os.path.join(build_dir, cefclient_app),
              os.path.join(dst_dir, cefclient_app), options.quiet)
          # Replace the versioned framework with an unversioned framework in the sample app.
          framework_dst_dir = os.path.join(
              dst_dir, '%s/Contents/Frameworks/%s.framework' % (cefclient_app,
                                                                framework_name))
          remove_dir(framework_dst_dir, options.quiet)
        copy_dir(framework_src_dir, framework_dst_dir, options.quiet)

    if not options.nosymbols and mode != 'debug-symbols':
      # create the symbol output directory
      symbol_output_dir = create_output_dir(
          output_dir_name + '_release_symbols', options.outputdir)

      # The real dSYM already exists, just copy it to the output directory.
      # dSYMs are only generated when is_official_build=true or enable_dsyms=true.
      # See //build/config/mac/symbols.gni.
      for dsym in dsym_dirs:
        copy_dir(
            os.path.join(build_dir, dsym),
            os.path.join(symbol_output_dir, dsym), options.quiet)
  else:
    sys.stdout.write("No Release build files.\n")

  if mode == 'standard' or mode == 'minimal':
    # transfer include files
    transfer_gypi_files(cef_dir, cef_paths2['includes_mac'], \
                        'include/', include_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['includes_mac_capi'], \
                        'include/', include_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['includes_wrapper_mac'], \
                        'include/', include_dir, options.quiet)

    # transfer libcef_dll_wrapper files
    transfer_gypi_files(cef_dir, cef_paths2['libcef_dll_wrapper_sources_mac'], \
                        'libcef_dll/', libcef_dll_dir, options.quiet)

    # transfer additional files, if any
    transfer_files(cef_dir, script_dir, os.path.join(script_dir, 'distrib', 'mac'), \
                   mode, output_dir, options.quiet)

  if mode == 'standard':
    # transfer shared files
    transfer_gypi_files(cef_dir, cef_paths2['shared_sources_mac'], \
                        'tests/shared/', shared_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['shared_sources_mac_helper'], \
                        'tests/shared/', shared_dir, options.quiet)

    # transfer cefclient files
    transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_mac'], \
                        'tests/cefclient/', cefclient_dir, options.quiet)

    # transfer cefclient/mac files
    copy_dir(os.path.join(cef_dir, 'tests/cefclient/mac'), \
             os.path.join(cefclient_dir, 'mac'), \
             options.quiet)

    # transfer cefsimple files
    transfer_gypi_files(cef_dir, cef_paths2['cefsimple_sources_mac'], \
                        'tests/cefsimple/', cefsimple_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['cefsimple_sources_mac_helper'], \
                        'tests/cefsimple/', cefsimple_dir, options.quiet)

    # transfer cefsimple/mac files
    copy_dir(os.path.join(cef_dir, 'tests/cefsimple/mac'), \
             os.path.join(cefsimple_dir, 'mac'), \
             options.quiet)

    # transfer ceftests files
    transfer_gypi_files(cef_dir, cef_paths2['ceftests_sources_mac'], \
                        'tests/ceftests/', ceftests_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['ceftests_sources_mac_helper'], \
                        'tests/ceftests/', ceftests_dir, options.quiet)

    # transfer ceftests/mac files
    copy_dir(os.path.join(cef_dir, 'tests/ceftests/mac'), \
             os.path.join(ceftests_dir, 'mac'), \
             options.quiet)

elif platform == 'linux':
  libcef_so = 'libcef.so'
  # yapf: disable
  binaries = [
      {'path': 'chrome_sandbox', 'out_path': 'chrome-sandbox'},
      {'path': libcef_so},
      {'path': 'libEGL.so'},
      {'path': 'libGLESv2.so'},
      {'path': 'libvk_swiftshader.so'},
      {'path': 'libvulkan.so.1'},
      {'path': 'v8_context_snapshot.bin'},
      {'path': 'vk_swiftshader_icd.json'},
  ]
  # yapf: enable
  if options.ozone:
    binaries.append({'path': 'libminigbm.so', 'conditional': True})

  if mode == 'client':
    binaries.append({'path': 'cefsimple'})

  # yapf: disable
  resources = [
      {'path': 'chrome_100_percent.pak'},
      {'path': 'chrome_200_percent.pak'},
      {'path': 'resources.pak'},
      {'path': 'icudtl.dat'},
      {'path': 'locales', 'delete': '*.info'},
  ]
  # yapf: enable

  valid_build_dir = None

  if mode == 'standard':
    # transfer Debug files
    build_dir = build_dir_debug
    libcef_path = os.path.join(build_dir, libcef_so)
    if not options.allowpartial or path_exists(libcef_path):
      valid_build_dir = build_dir
      dst_dir = os.path.join(output_dir, 'Debug')
      copy_files_list(build_dir, dst_dir, binaries)
    else:
      sys.stdout.write("No Debug build files.\n")

  # transfer Release files
  build_dir = build_dir_release
  libcef_path = os.path.join(build_dir, libcef_so)
  if not options.allowpartial or path_exists(libcef_path):
    valid_build_dir = build_dir
    dst_dir = os.path.join(output_dir, 'Release')
    copy_files_list(build_dir, dst_dir, binaries)
  else:
    sys.stdout.write("No Release build files.\n")

  if not valid_build_dir is None:
    # transfer resource files
    build_dir = valid_build_dir
    if mode == 'client':
      dst_dir = os.path.join(output_dir, 'Release')
    else:
      dst_dir = os.path.join(output_dir, 'Resources')
    copy_files_list(build_dir, dst_dir, resources)

  if mode == 'standard' or mode == 'minimal':
    # transfer include files
    transfer_gypi_files(cef_dir, cef_paths2['includes_linux'], \
                        'include/', include_dir, options.quiet)
    transfer_gypi_files(cef_dir, cef_paths2['includes_linux_capi'], \
                        'include/', include_dir, options.quiet)

    # transfer additional files, if any
    transfer_files(cef_dir, script_dir, os.path.join(script_dir, 'distrib', 'linux'), \
                   mode, output_dir, options.quiet)

  if mode == 'standard':
    # transfer shared files
    transfer_gypi_files(cef_dir, cef_paths2['shared_sources_linux'], \
                        'tests/shared/', shared_dir, options.quiet)

    if not options.ozone:
      # transfer cefclient files
      transfer_gypi_files(cef_dir, cef_paths2['cefclient_sources_linux'], \
                          'tests/cefclient/', cefclient_dir, options.quiet)

    # transfer cefsimple files
    transfer_gypi_files(cef_dir, cef_paths2['cefsimple_sources_linux'], \
                        'tests/cefsimple/', cefsimple_dir, options.quiet)

    # transfer ceftests files
    transfer_gypi_files(cef_dir, cef_paths2['ceftests_sources_linux'], \
                        'tests/ceftests/', ceftests_dir, options.quiet)

if mode == 'standard' or mode == 'minimal':
  variables = {
      'version_long': version_formatter.get_version_string(),
      'version_short': version_formatter.get_short_version_string(),
      'version_plist': version_formatter.get_plist_version_string(),
  }
  variables.update(cef_paths2)

  copy_dir(
      os.path.join(cef_dir, 'bazel'),
      os.path.join(output_dir, 'bazel'), options.quiet)

  transfer_bazel_files(
      os.path.join(script_dir, 'distrib', 'bazel'),
      output_dir,
      variables,
      require_parent_dir=(mode != 'standard'))

if not options.noarchive:
  # create an archive for each output directory
  archive_format = os.getenv('CEF_ARCHIVE_FORMAT', 'zip')
  if archive_format not in ('zip', 'tar.gz', 'tar.bz2'):
    raise Exception('Unsupported archive format: %s' % archive_format)

  if os.getenv('CEF_COMMAND_7ZIP', '') != '':
    archive_format = os.getenv('CEF_COMMAND_7ZIP_FORMAT', '7z')

  for dir in archive_dirs:
    if not options.quiet:
      sys.stdout.write("Creating %s archive for %s...\n" %
                       (archive_format, os.path.basename(dir)))
    if archive_format == 'zip':
      create_zip_archive(dir)
    elif archive_format == 'tar.gz':
      create_tar_archive(dir, 'gz')
    elif archive_format == 'tar.bz2':
      create_tar_archive(dir, 'bz2')
    else:
      create_7z_archive(dir, archive_format)
