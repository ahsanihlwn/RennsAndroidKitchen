# SPDX-License-Identifier: MPL-2.0
"""Kitchen tool vocabulary; ordinary file work uses the agent's own tools."""
PRIMARY = {
    'discover': ['doctor --json', 'inspect [-L] PATH --json', 'readlink [-f|-e] LINK', 'context --json', 'capabilities --json'],
    'kitchen': ['unpack FILE', 'repack FILESYSTEM_ROOT', 'repack FILESYSTEM_ROOT -o OUTPUT.img',
                'repack FILESYSTEM_ROOT -o OUTPUT.img.br', 'repack FILESYSTEM_ROOT -o OUTPUT.new.dat',
                'repack FILESYSTEM_ROOT -o OUTPUT.new.dat.br',
                'mount FILESYSTEM.img', 'unmount FILESYSTEM.img', 'repack FOLDER -o OUTPUT.zip'],
    'metadata': ['stat [-L] PATH... [--json]', 'fs context PATH SELINUX_LABEL', 'fs permission PATH OCTAL_MODE',
                 'fs make-filesystem FOLDER --type ext4 --yes'],
    'state': ['branch current FOLDER', 'branch create FOLDER NAME', 'branch switch FOLDER NAME',
              'activity status --json', 'activity show TASK_ID --json', 'events --since CURSOR --wait 30 --json'],
}


def help_text():
    lines=['RAK: Android Kitchen tools and NeoBranch checkout.',
           'Use your native Read/Write/Edit, shell or Python for ordinary files, including active physical branches.',
           'Start with: rak inspect PATH --json (returns exact usable command argv).', '']
    for group,commands in PRIMARY.items():
        lines.append(group+':')
        lines.extend('  rak '+command for command in commands)
    lines.extend(['', 'NeoBranch checks out partition files and config into their normal project paths.',
                  'BR/DAT -> IMG; payload/SUPER -> partition IMG; filesystem IMG -> folder -> rebuilt IMG.',
                  'Mount accepts supported filesystem IMG, not BR/DAT/payload containers.',
                  'Virtual image mounts use rak img; Android labels/modes use fs context/permission.',
                  'Compatibility file commands remain available through rak list --all.',
                  'Run rak list --all for compatibility actions and advanced format commands.'])
    return '\n'.join(lines)
