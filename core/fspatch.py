#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright by affggh
# This program based on Apache 2.0 LICENES
import os

VERBOSE = False
AUTHOR = "affggh"
LICENES = "Apache 2.0"
VERSION = "1.0"

print("[CORE STAGE] FILESYSTEM PERMISSION WORKER - by affggh")

def scanfs(file):  # 读取fs_config文件返回一个字典
    fsconfig = {}
    with open(file, "r", encoding="utf-8", errors="ignore") as fsfile:
        for i in fsfile.readlines():
            parts = i.strip().split()
            if len(parts) < 4:
                continue
            filepath, uid, gid, mode = parts[0], parts[1], parts[2], parts[3]
            fsconfig[filepath] = [uid, gid, mode]
    return fsconfig

def has_extra_fields(file):
    with open(file, "r", encoding="utf-8", errors="ignore") as fsfile:
        for i in fsfile:
            s = i.strip()
            if not s or s.startswith("#"):
                continue
            if len(s.split()) > 4:
                return True
    return False

def scanfsdir(folder):  # 读取解包的目录，返回一个字典
    allfile = []
    allfile.append('/')
    if os.name == 'nt':
        allfile.append(os.path.basename(folder).replace('\\',''))
    elif os.name == 'posix':
        allfile.append(os.path.basename(folder).replace('/',''))
    else:
        return False
    for root,dirs,files in os.walk(folder):
        for dir in dirs:
            if os.name == 'nt':
                allfile.append(os.path.join(root,dir).replace(folder,os.path.basename(folder)).replace('\\','/'))
            elif os.name == 'posix':
                allfile.append(os.path.join(root,dir).replace(folder,os.path.basename(folder)))
        for file in files:
            if os.name == 'nt':
                allfile.append(os.path.join(root,file).replace(folder,os.path.basename(folder)).replace('\\','/'))
            elif os.name == 'posix':
                allfile.append(os.path.join(root,file).replace(folder,os.path.basename(folder)))
    return allfile

def islink(file):
    if os.name == 'nt':
        if not os.path.isdir(file):
            with open(file, 'rb') as f:
                magic = f.read(12)
                if magic == b'!<symlink>\xff\xfe':
                    point =  f.read()
                    return point.decode("utf-8").replace('\x00','')
                else:
                    return False
    if os.name == 'posix':
        if os.path.islink(file):
            return os.readlink(file)
        else:
            return False

def fspatch(fsfile, filename, dirpath):  # 接收两个字典对比
    newfs = {}
    for i in filename:
        if fsfile.get(i):
            newfs.update({i: fsfile[i]})
        else:
            if os.name == 'nt':
                filepath = os.path.abspath(dirpath+os.sep+".."+os.sep+i.replace('/', '\\'))
            elif os.name == 'posix':
                filepath = os.path.abspath(dirpath+os.sep+".."+os.sep+i)
            if os.path.isdir(filepath):
                uid = '0'
                if (i.find("system/bin")!=-1 or i.find("system/xbin")!=-1):
                    gid = '2000'
                elif (i.find("vendor/bin")!=-1):
                    gid = '2000'
                else:
                    gid = '0'
                mode = '0755'  # dir path always 755
                config = [uid, gid, mode]
            elif islink(filepath):
                uid = '0'
                if (i.find("system/bin")!=-1) or (i.find("system/xbin")!=-1) or (i.find("vendor/bin")!=-1):
                    gid = '2000'
                else:
                    gid = '0'
                if (i.find("/bin")!=-1) or (i.find("/xbin")!=-1):
                    mode = '0755'
                elif (i.find(".sh")!=-1):
                    mode = "0750"
                else:
                    mode = "0644"
                config = [uid, gid, mode]
            elif (i.find("/bin")!=-1) or (i.find("/xbin")!=-1):
                uid = '0'

                # default aman
                gid = '0'
                mode = '0755'

                if (i.find("system/bin")!=-1) or \
                (i.find("system/xbin")!=-1) or \
                (i.find("vendor/bin")!=-1):
                    gid = '2000'

                if (i.find(".sh")!=-1):
                    mode = "0750"

                config = [uid, gid, mode]
            else:
                uid = '0'
                gid = '0'
                mode = '0644'
                config = [uid, gid, mode]
            newfs.update({i: config})
            if VERBOSE:
                print("Add file [%s] fs_config: uid[%s] gid[%s] mode[%s]" %(i,config[0],config[1],config[2]))
    return newfs

def writetofile(file, newfsconfig):
    with open(file, "w", encoding="utf-8") as f:
        for i in list(sorted(newfsconfig.keys())):
            fs = i+' '+newfsconfig[i][0]+' '+newfsconfig[i][1]+' '+newfsconfig[i][2]+"\n"
            f.write(fs)

def main(dirpath, fsconfig):
    origfs = scanfs(os.path.abspath(fsconfig))
    allfile = scanfsdir(os.path.abspath(dirpath))
    newfs = fspatch(origfs, allfile, dirpath)
    writetofile(fsconfig, newfs)
    print("Load origin %d" %(len(origfs.keys()))+" entrys")
    print("Detect totsl %d" %(len(allfile))+ " entrys")
    print("New fs_config %d" %(len(newfs.keys()))+" entrys")

def Usage():
    print("Usage:")
    print("%s <folder> <fs_config>" %(sys.argv[0]))
    print("    This script will auto patch fs_config")

if __name__ == '__main__':
    import sys

    VERBOSE = False
    if len(sys.argv) < 3:
        Usage()
        sys.exit()
    if len(sys.argv) > 3:
        if sys.argv[3] == '-i':
            VERBOSE = True
    if (os.path.isdir(sys.argv[1]) or os.path.isfile(sys.argv[2])):
        print("FSPATCH by [%s]\nLICENES [%s]\nVERSION [%s]" %(AUTHOR, LICENES, VERSION))
        main(sys.argv[1], sys.argv[2])
        print("Done!")
    else:
        print("The path or filetype you have given may wrong,please check it wether correct.")
        Usage()
