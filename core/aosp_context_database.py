# SPDX-License-Identifier: MPL-2.0
import re


AOSP_ROOTFS_EXACT = {
    "/":                                        "u:object_r:rootfs:s0",
    "/init":                                    "u:object_r:init_exec:s0",
    "/adb_keys":                                "u:object_r:system_file:s0",
    "/build.prop":                              "u:object_r:rootfs:s0",
    "/default.prop":                            "u:object_r:rootfs:s0",
    "/selinux_version":                         "u:object_r:rootfs:s0",
    "/verity_key":                              "u:object_r:rootfs:s0",
    "/lost+found":                              "u:object_r:rootfs:s0",
    "/acct":                                    "u:object_r:cgroup:s0",
    "/apex":                                    "u:object_r:apex_mnt_dir:s0",
    "/bootstrap-apex":                          "u:object_r:apex_mnt_dir:s0",
    "/data_mirror":                             "u:object_r:mirror_data_file:s0",
    "/debug_ramdisk":                           "u:object_r:tmpfs:s0",
    "/mnt":                                     "u:object_r:tmpfs:s0",
    "/second_stage_resources":                  "u:object_r:tmpfs:s0",
    "/sys":                                     "u:object_r:sysfs:s0",
    "/tmp":                                     "u:object_r:shell_data_file:s0",
    "/postinstall":                             "u:object_r:postinstall_mnt_dir:s0",
    "/postinstall/apex":                        "u:object_r:postinstall_apex_mnt_dir:s0",
    # rootfs symlinks
    "/bin":                                     "u:object_r:rootfs:s0",
    "/bugreports":                              "u:object_r:rootfs:s0",
    "/charger":                                 "u:object_r:rootfs:s0",
    "/d":                                       "u:object_r:rootfs:s0",
    "/etc":                                     "u:object_r:rootfs:s0",
    "/sdcard":                                  "u:object_r:rootfs:s0",
    "/proc":                                    "u:object_r:rootfs:s0",
    "/config":                                  "u:object_r:rootfs:s0",
    # linker config
    "/linkerconfig":                            "u:object_r:linkerconfig_file:s0",
    # SELinux policy files at root
    "/vendor_file_contexts":                    "u:object_r:file_contexts_file:s0",
    "/plat_file_contexts":                      "u:object_r:file_contexts_file:s0",
    "/product_file_contexts":                   "u:object_r:file_contexts_file:s0",
    "/mapping_sepolicy.cil":                    "u:object_r:sepolicy_file:s0",
    "/plat_sepolicy.cil":                       "u:object_r:sepolicy_file:s0",
    "/plat_property_contexts":                  "u:object_r:property_contexts_file:s0",
    "/product_property_contexts":               "u:object_r:property_contexts_file:s0",
    "/vendor_property_contexts":                "u:object_r:property_contexts_file:s0",
    "/seapp_contexts":                          "u:object_r:seapp_contexts_file:s0",
    "/vendor_seapp_contexts":                   "u:object_r:seapp_contexts_file:s0",
    "/plat_seapp_contexts":                     "u:object_r:seapp_contexts_file:s0",
    "/sepolicy":                                "u:object_r:sepolicy_file:s0",
    "/plat_tee_service_contexts":               "u:object_r:tee_service_contexts_file:s0",
    "/plat_service_contexts":                   "u:object_r:service_contexts_file:s0",
    "/plat_hwservice_contexts":                 "u:object_r:hwservice_contexts_file:s0",
    "/plat_keystore2_key_contexts":             "u:object_r:keystore2_key_contexts_file:s0",
    "/vendor_service_contexts":                 "u:object_r:vendor_service_contexts_file:s0",
    "/vendor_hwservice_contexts":               "u:object_r:hwservice_contexts_file:s0",
    "/vndservice_contexts":                     "u:object_r:vndservice_contexts_file:s0",
}

AOSP_ROOTFS_PREFIX = [
    ("/linkerconfig/",                          "u:object_r:linkerconfig_file:s0"),
    ("/system_dlkm/",                           "u:object_r:system_dlkm_file:s0"),
    ("/apex/",                                  "u:object_r:apex_mnt_dir:s0"),
    ("/sbin/",                                  "u:object_r:rootfs:s0"),
    ("/lib/",                                   "u:object_r:rootfs:s0"),
    ("/res/",                                   "u:object_r:rootfs:s0"),
]

AOSP_ROOTFS_REGEX = [
    (re.compile(r"^/apex/(\.(bootstrap|default)-)?apex-info-list\.xml$"), "u:object_r:apex_info_file:s0"),
    (re.compile(r"^/fstab\."),                  "u:object_r:rootfs:s0"),
    (re.compile(r"^/init\."),                   "u:object_r:rootfs:s0"),
    (re.compile(r"^/ueventd\."),                "u:object_r:rootfs:s0"),
    (re.compile(r"^/second_stage_resources/overlay_remounter$"), "u:object_r:overlay_remounter_exec:s0"),
]


AOSP_SYSTEM_EXACT = {
    # catch-all prefix untuk ketiga partisi (exact = root dir saja)
    # Exact path untuk ART APEX — hanya /system, bukan system_ext/product
    "/system/apex/com.android.art":             "u:object_r:art_apex_dir:s0",

    # linkerconfig (exact, bukan regex)
    "/system/bin/linkerconfig":                 "u:object_r:linkerconfig_exec:s0",
    "/system/bin/bootstrap/linkerconfig":       "u:object_r:linkerconfig_exec:s0",

    # Core executables
    "/system/bin/init":                         "u:object_r:init_exec:s0",
    "/system/bin/app_process32":                "u:object_r:zygote_exec:s0",
    "/system/bin/app_process64":                "u:object_r:zygote_exec:s0",
    "/system/bin/servicemanager":               "u:object_r:servicemanager_exec:s0",
    "/system/bin/surfaceflinger":               "u:object_r:surfaceflinger_exec:s0",
    "/system/bin/gpuservice":                   "u:object_r:gpuservice_exec:s0",
    "/system/bin/bufferhubd":                   "u:object_r:bufferhubd_exec:s0",
    "/system/bin/performanced":                 "u:object_r:performanced_exec:s0",
    "/system/bin/vold":                         "u:object_r:vold_exec:s0",
    "/system/bin/netd":                         "u:object_r:netd_exec:s0",
    "/system/bin/wificond":                     "u:object_r:wificond_exec:s0",
    "/system/bin/ot-ctl":                       "u:object_r:ot_ctl_exec:s0",
    "/system/bin/audioserver":                  "u:object_r:audioserver_exec:s0",
    "/system/bin/mediadrmserver":               "u:object_r:mediadrmserver_exec:s0",
    "/system/bin/mediaserver":                  "u:object_r:mediaserver_exec:s0",
    "/system/bin/mediaserver32":                "u:object_r:mediaserver_exec:s0",
    "/system/bin/mediaserver64":                "u:object_r:mediaserver_exec:s0",
    "/system/bin/mediametrics":                 "u:object_r:mediametrics_exec:s0",
    "/system/bin/cameraserver":                 "u:object_r:cameraserver_exec:s0",
    "/system/bin/mediaextractor":               "u:object_r:mediaextractor_exec:s0",
    "/system/bin/mediaswcodec":                 "u:object_r:mediaswcodec_exec:s0",
    "/system/bin/mediatranscoding":             "u:object_r:mediatranscoding_exec:s0",
    "/system/bin/mediatuner":                   "u:object_r:mediatuner_exec:s0",
    "/system/bin/drmserver":                    "u:object_r:drmserver_exec:s0",
    "/system/bin/drmserver32":                  "u:object_r:drmserver_exec:s0",
    "/system/bin/drmserver64":                  "u:object_r:drmserver_exec:s0",
    "/system/bin/dumpstate":                    "u:object_r:dumpstate_exec:s0",
    "/system/bin/incident":                     "u:object_r:incident_exec:s0",
    "/system/bin/incidentd":                    "u:object_r:incidentd_exec:s0",
    "/system/bin/incident_helper":              "u:object_r:incident_helper_exec:s0",
    "/system/bin/iw":                           "u:object_r:iw_exec:s0",
    "/system/bin/netutils-wrapper-1.0":         "u:object_r:netutils_wrapper_exec:s0",
    "/system/bin/bootanimation":                "u:object_r:bootanim_exec:s0",
    "/system/bin/bootstat":                     "u:object_r:bootstat_exec:s0",
    "/system/bin/sh":                           "u:object_r:shell_exec:s0",
    "/system/bin/run-as":                       "u:object_r:runas_exec:s0",
    "/system/bin/toolbox":                      "u:object_r:toolbox_exec:s0",
    "/system/bin/toybox":                       "u:object_r:toolbox_exec:s0",
    "/system/bin/mini-keyctl":                  "u:object_r:toolbox_exec:s0",
    "/system/bin/logcat":                       "u:object_r:logcat_exec:s0",
    "/system/bin/logcatd":                      "u:object_r:logcat_exec:s0",
    "/system/bin/atrace":                       "u:object_r:atrace_exec:s0",
    "/system/bin/auditctl":                     "u:object_r:auditctl_exec:s0",
    "/system/bin/bcc":                          "u:object_r:rs_exec:s0",
    "/system/bin/ld.mc":                        "u:object_r:rs_exec:s0",
    "/system/bin/blank_screen":                 "u:object_r:blank_screen_exec:s0",
    "/system/bin/prng_seeder":                  "u:object_r:prng_seeder_exec:s0",
    "/system/bin/charger":                      "u:object_r:charger_exec:s0",
    "/system/bin/e2fsdroid":                    "u:object_r:e2fs_exec:s0",
    "/system/bin/mke2fs":                       "u:object_r:e2fs_exec:s0",
    "/system/bin/sload_f2fs":                   "u:object_r:e2fs_exec:s0",
    "/system/bin/make_f2fs":                    "u:object_r:e2fs_exec:s0",
    "/system/bin/e2fsck":                       "u:object_r:fsck_exec:s0",
    "/system/bin/fsck.exfat":                   "u:object_r:fsck_exec:s0",
    "/system/bin/fsck.f2fs":                    "u:object_r:fsck_exec:s0",
    "/system/bin/fsck_msdos":                   "u:object_r:fsck_exec:s0",
    "/system/bin/tune2fs":                      "u:object_r:fsck_exec:s0",
    "/system/bin/resize2fs":                    "u:object_r:fsck_exec:s0",
    "/system/bin/tcpdump":                      "u:object_r:tcpdump_exec:s0",
    "/system/bin/mm_events":                    "u:object_r:mm_events_exec:s0",
    "/system/bin/extra_free_kbytes.sh":         "u:object_r:extra_free_kbytes_exec:s0",
    "/system/bin/mdnsd":                        "u:object_r:mdnsd_exec:s0",
    "/system/bin/installd":                     "u:object_r:installd_exec:s0",
    "/system/bin/otapreopt_chroot":             "u:object_r:otapreopt_chroot_exec:s0",
    "/system/bin/otapreopt_slot":               "u:object_r:otapreopt_slot_exec:s0",
    "/system/bin/credstore":                    "u:object_r:credstore_exec:s0",
    "/system/bin/keystore":                     "u:object_r:keystore_exec:s0",
    "/system/bin/keystore2":                    "u:object_r:keystore_exec:s0",
    "/system/bin/fingerprintd":                 "u:object_r:fingerprintd_exec:s0",
    "/system/bin/gatekeeperd":                  "u:object_r:gatekeeperd_exec:s0",
    "/system/bin/tombstoned":                   "u:object_r:tombstoned_exec:s0",
    "/system/bin/recovery-persist":             "u:object_r:recovery_persist_exec:s0",
    "/system/bin/recovery-refresh":             "u:object_r:recovery_refresh_exec:s0",
    "/system/bin/sdcard":                       "u:object_r:sdcardd_exec:s0",
    "/system/bin/snapshotctl":                  "u:object_r:snapshotctl_exec:s0",
    "/system/bin/remount":                      "u:object_r:remount_exec:s0",
    "/system/bin/dhcpcd":                       "u:object_r:dhcp_exec:s0",
    "/system/bin/dhcpcd-6.8.2":                "u:object_r:dhcp_exec:s0",
    "/system/bin/dmesgd":                       "u:object_r:dmesgd_exec:s0",
    "/system/bin/dnsmasq":                      "u:object_r:dnsmasq_exec:s0",
    "/system/bin/llkd":                         "u:object_r:llkd_exec:s0",
    "/system/bin/lmkd":                         "u:object_r:lmkd_exec:s0",
    "/system/bin/mmd":                          "u:object_r:mmd_exec:s0",
    "/system/bin/usbd":                         "u:object_r:usbd_exec:s0",
    "/system/bin/inputflinger":                 "u:object_r:inputflinger_exec:s0",
    "/system/bin/logd":                         "u:object_r:logd_exec:s0",
    "/system/bin/lpdumpd":                      "u:object_r:lpdumpd_exec:s0",
    "/system/bin/rss_hwm_reset":                "u:object_r:rss_hwm_reset_exec:s0",
    "/system/bin/perfetto":                     "u:object_r:perfetto_exec:s0",
    "/system/bin/mtectrl":                      "u:object_r:mtectrl_exec:s0",
    "/system/bin/misctrl":                      "u:object_r:misctrl_exec:s0",
    "/system/bin/kcmdlinectrl":                 "u:object_r:kcmdlinectrl_exec:s0",
    "/system/bin/traced":                       "u:object_r:traced_exec:s0",
    "/system/bin/traced_perf":                  "u:object_r:traced_perf_exec:s0",
    "/system/bin/traced_probes":                "u:object_r:traced_probes_exec:s0",
    "/system/bin/traced_relay":                 "u:object_r:traced_exec:s0",
    "/system/bin/trace_redactor":               "u:object_r:trace_redactor_exec:s0",
    "/system/bin/heapprofd":                    "u:object_r:heapprofd_exec:s0",
    "/system/bin/uncrypt":                      "u:object_r:uncrypt_exec:s0",
    "/system/bin/update_verifier":              "u:object_r:update_verifier_exec:s0",
    "/system/bin/logwrapper":                   "u:object_r:system_file:s0",
    "/system/bin/vdc":                          "u:object_r:vdc_exec:s0",
    "/system/bin/android.frameworks.automotive.display@1.0-service": "u:object_r:automotive_display_service_exec:s0",
    "/system/bin/cppreopts.sh":                 "u:object_r:cppreopts_exec:s0",
    "/system/bin/preloads_copy.sh":             "u:object_r:preloads_copy_exec:s0",
    "/system/bin/preopt2cachename":             "u:object_r:preopt2cachename_exec:s0",
    "/system/bin/sgdisk":                       "u:object_r:sgdisk_exec:s0",
    "/system/bin/blkid":                        "u:object_r:blkid_exec:s0",
    "/system/bin/pbtombstone":                  "u:object_r:pbtombstone_exec:s0",
    "/system/bin/update_engine":                "u:object_r:update_engine_exec:s0",
    "/system/bin/update_engine_nostats":        "u:object_r:update_engine_exec:s0",
    "/system/bin/profcollectd":                 "u:object_r:profcollectd_exec:s0",
    "/system/bin/profcollectctl":               "u:object_r:profcollectd_exec:s0",
    "/system/bin/storaged":                     "u:object_r:storaged_exec:s0",
    "/system/bin/virtual_touchpad":             "u:object_r:virtual_touchpad_exec:s0",
    "/system/bin/virtual_camera":               "u:object_r:virtual_camera_exec:s0",
    "/system/bin/adbd":                         "u:object_r:adbd_exec:s0",
    "/system/bin/vold_prepare_subdirs":         "u:object_r:vold_prepare_subdirs_exec:s0",
    "/system/bin/stats":                        "u:object_r:stats_exec:s0",
    "/system/bin/statsd":                       "u:object_r:statsd_exec:s0",
    "/system/bin/bpfloader":                    "u:object_r:bpfloader_exec:s0",
    "/system/bin/netbpfload":                   "u:object_r:bpfloader_exec:s0",
    "/system/bin/watchdogd":                    "u:object_r:watchdogd_exec:s0",
    "/system/bin/aconfigd-system":              "u:object_r:aconfigd_exec:s0",
    "/system/bin/apexd":                        "u:object_r:apexd_exec:s0",
    "/system/bin/gsid":                         "u:object_r:gsid_exec:s0",
    "/system/bin/simpleperf":                   "u:object_r:simpleperf_exec:s0",
    "/system/bin/simpleperf_app_runner":        "u:object_r:simpleperf_app_runner_exec:s0",
    "/system/bin/migrate_legacy_obb_data":      "u:object_r:migrate_legacy_obb_data_exec:s0",
    "/system/bin/snapuserd":                    "u:object_r:snapuserd_exec:s0",
    "/system/bin/odsign":                       "u:object_r:odsign_exec:s0",
    "/system/bin/vehicle_binding_util":         "u:object_r:vehicle_binding_util_exec:s0",
    "/system/bin/cardisplayproxyd":             "u:object_r:automotive_display_service_exec:s0",
    "/system/bin/evsmanagerd":                  "u:object_r:evsmanagerd_exec:s0",
    "/system/bin/uprobestats":                  "u:object_r:uprobestats_exec:s0",
    "/system/bin/bert_collector":               "u:object_r:bert_collector_exec:s0",
    "/system/bin/linux_vm_setup":               "u:object_r:linux_vm_setup_exec:s0",
    "/system/bin/tradeinmode":                  "u:object_r:tradeinmode_exec:s0",
    "/system/bin/prefetch":                     "u:object_r:prefetch_exec:s0",
    "/system/bin/memcgv2_activation_depth.sh":  "u:object_r:memcgv2_activation_depth_exec:s0",
    "/system/xbin/su":                          "u:object_r:su_exec:s0",
    # /system/bin/hw
    "/system/bin/hw/android.frameworks.bufferhub@1.0-service":     "u:object_r:fwk_bufferhub_exec:s0",
    "/system/bin/hw/android.system.suspend-service":                "u:object_r:system_suspend_exec:s0",
    "/system/bin/android.frameworks.automotive.display@1.0-service": "u:object_r:automotive_display_service_exec:s0",
    # /system/etc
    "/system/etc/cgroups.json":                         "u:object_r:cgroup_desc_file:s0",
    "/system/etc/event-log-tags":                       "u:object_r:system_event_log_tags_file:s0",
    "/system/etc/font_fallback.xml":                    "u:object_r:system_font_fallback_file:s0",
    "/system/etc/group":                                "u:object_r:system_group_file:s0",
    "/system/etc/passwd":                               "u:object_r:system_passwd_file:s0",
    "/system/etc/task_profiles.json":                   "u:object_r:task_profiles_file:s0",
    "/system/etc/selinux/plat_mac_permissions.xml":     "u:object_r:mac_perms_file:s0",
    "/system/etc/selinux/plat_property_contexts":       "u:object_r:property_contexts_file:s0",
    "/system/etc/selinux/plat_tee_service_contexts":    "u:object_r:tee_service_contexts_file:s0",
    "/system/etc/selinux/plat_service_contexts":        "u:object_r:service_contexts_file:s0",
    "/system/etc/selinux/plat_hwservice_contexts":      "u:object_r:hwservice_contexts_file:s0",
    "/system/etc/selinux/plat_keystore2_key_contexts":  "u:object_r:keystore2_key_contexts_file:s0",
    "/system/etc/selinux/plat_file_contexts":           "u:object_r:file_contexts_file:s0",
    "/system/etc/selinux/plat_seapp_contexts":          "u:object_r:seapp_contexts_file:s0",
    "/system/etc/selinux/plat_sepolicy.cil":            "u:object_r:sepolicy_file:s0",
    "/system/etc/selinux/plat_and_mapping_sepolicy.cil.sha256": "u:object_r:sepolicy_file:s0",
    # product-specific exact — AOSP format: /(product|system/product)/...
    "/product/etc/group":                               "u:object_r:system_group_file:s0",
    "/product/etc/passwd":                              "u:object_r:system_passwd_file:s0",
    "/product/etc/selinux/product_file_contexts":       "u:object_r:file_contexts_file:s0",
    "/product/etc/selinux/product_hwservice_contexts":  "u:object_r:hwservice_contexts_file:s0",
    "/product/etc/selinux/product_keystore2_key_contexts": "u:object_r:keystore2_key_contexts_file:s0",
    "/product/etc/selinux/product_property_contexts":   "u:object_r:property_contexts_file:s0",
    "/product/etc/selinux/product_seapp_contexts":      "u:object_r:seapp_contexts_file:s0",
    "/product/etc/selinux/product_service_contexts":    "u:object_r:service_contexts_file:s0",
    "/product/etc/selinux/product_mac_permissions.xml": "u:object_r:mac_perms_file:s0",
    "/product/etc/selinux/product_tee_service_contexts": "u:object_r:tee_service_contexts_file:s0",
    "/product/etc/security/adb_keys":                   "u:object_r:adb_keys_file:s0",
    # system_ext-specific exact — AOSP format: /(system_ext|system/system_ext)/...
    "/system_ext/etc/group":                            "u:object_r:system_group_file:s0",
    "/system_ext/etc/passwd":                           "u:object_r:system_passwd_file:s0",
    "/system_ext/etc/selinux/system_ext_file_contexts":        "u:object_r:file_contexts_file:s0",
    "/system_ext/etc/selinux/system_ext_hwservice_contexts":   "u:object_r:hwservice_contexts_file:s0",
    "/system_ext/etc/selinux/system_ext_keystore2_key_contexts": "u:object_r:keystore2_key_contexts_file:s0",
    "/system_ext/etc/selinux/system_ext_property_contexts":    "u:object_r:property_contexts_file:s0",
    "/system_ext/etc/selinux/system_ext_seapp_contexts":       "u:object_r:seapp_contexts_file:s0",
    "/system_ext/etc/selinux/system_ext_service_contexts":     "u:object_r:service_contexts_file:s0",
    "/system_ext/etc/selinux/system_ext_mac_permissions.xml":  "u:object_r:mac_perms_file:s0",
    "/system_ext/etc/selinux/userdebug_plat_sepolicy.cil":     "u:object_r:sepolicy_file:s0",
    "/system_ext/etc/selinux/system_ext_tee_service_contexts": "u:object_r:tee_service_contexts_file:s0",
    # system_ext executables
    "/system_ext/bin/aidl_lazy_test_server":             "u:object_r:aidl_lazy_test_server_exec:s0",
    "/system_ext/bin/aidl_lazy_cb_test_server":          "u:object_r:aidl_lazy_test_server_exec:s0",
    "/system_ext/bin/hidl_lazy_test_server":             "u:object_r:hidl_lazy_test_server_exec:s0",
    "/system_ext/bin/hidl_lazy_cb_test_server":          "u:object_r:hidl_lazy_test_server_exec:s0",
    "/system_ext/bin/hwservicemanager":                  "u:object_r:hwservicemanager_exec:s0",
    "/system_ext/bin/hw/android.hidl.allocator@1.0-service": "u:object_r:hal_allocator_default_exec:s0",
    "/system_ext/bin/rkp_cert_processor":                "u:object_r:rkp_cert_processor_exec:s0",
    "/system_ext/bin/hw/android.hardware.drm-service.widevine.system": "u:object_r:hal_widevine_system_exec:s0",
}


AOSP_SYSTEM_PREFIX = [
    # lib bootstrap
    ("/system/lib64/bootstrap/",                    "u:object_r:system_bootstrap_lib_file:s0"),
    ("/system/lib/bootstrap/",                      "u:object_r:system_bootstrap_lib_file:s0"),
    # lib
    ("/system/lib64/",                              "u:object_r:system_lib_file:s0"),
    ("/system/lib/",                                "u:object_r:system_lib_file:s0"),
    ("/system_ext/lib64/",                          "u:object_r:system_lib_file:s0"),
    ("/system_ext/lib/",                            "u:object_r:system_lib_file:s0"),
    ("/product/lib64/",                             "u:object_r:system_lib_file:s0"),
    ("/product/lib/",                               "u:object_r:system_lib_file:s0"),
    # etc subdirs — system
    ("/system/etc/aconfig/",                        "u:object_r:system_aconfig_storage_file:s0"),
    ("/system/etc/perfetto/",                       "u:object_r:system_perfetto_config_file:s0"),
    ("/system/etc/seccomp_policy/",                 "u:object_r:system_seccomp_policy_file:s0"),
    ("/system/etc/security/cacerts/",               "u:object_r:system_security_cacerts_file:s0"),
    ("/system/etc/ld.config",                       "u:object_r:system_linker_config_file:s0"),
    ("/system/etc/task_profiles/",                  "u:object_r:task_profiles_file:s0"),
    ("/system/usr/share/zoneinfo/",                 "u:object_r:system_zoneinfo_file:s0"),
    # etc subdirs — product
    ("/product/etc/aconfig/",                       "u:object_r:system_aconfig_storage_file:s0"),
    # etc subdirs — system_ext
    ("/system_ext/etc/aconfig/",                    "u:object_r:system_aconfig_storage_file:s0"),
    # overlay — system_ext overlay adalah vendor_overlay_file (sesuai AOSP)
    ("/system_ext/overlay/",                        "u:object_r:vendor_overlay_file:s0"),

    ("/system/",                                    "u:object_r:system_file:s0"),
    ("/system_ext/",                                "u:object_r:system_file:s0"),
    ("/product/",                                   "u:object_r:system_file:s0"),
]

AOSP_SYSTEM_REGEX = [
    (re.compile(r"^/system/bin/boringssl_self_test(?:32|64)$"),             "u:object_r:boringssl_self_test_exec:s0"),
    (re.compile(r"^/system/bin/flags_health_check$"),                       "u:object_r:flags_health_check_exec:s0"),
    (re.compile(r"^/system/bin/idmap2d?$"),                                 "u:object_r:idmap_exec:s0"),
    (re.compile(r"^/system/etc/selinux/mapping/\d+\.\d+(?:\.compat)?\.cil$"), "u:object_r:sepolicy_file:s0"),
    (re.compile(r"^/system/etc/task_profiles/cgroups_\d+\.json$"),          "u:object_r:cgroup_desc_file:s0"),
    (re.compile(r"^/system/etc/task_profiles/task_profiles_\d+\.json$"),    "u:object_r:task_profiles_file:s0"),
    (re.compile(r"^/system/bin/android\.automotive\.evs\.manager@1\.[0-9]+$"), "u:object_r:evsmanagerd_exec:s0"),
    (re.compile(r"^/system/bin/linker(?:64)?$"),                            "u:object_r:system_linker_exec:s0"),
    (re.compile(r"^/system/bin/bootstrap/linker(?:64)?$"),                  "u:object_r:system_linker_exec:s0"),
    # system_ext — AOSP: /(system_ext|system/system_ext)/bin/canhalconfigurator(-aidl)?
    (re.compile(r"^/system_ext/bin/canhalconfigurator(?:-aidl)?$"),         "u:object_r:canhalconfigurator_exec:s0"),
]


AOSP_VENDOR_EXACT = {
    # vendor executables
    "/vendor/bin/sh":                           "u:object_r:vendor_shell_exec:s0",
    "/vendor/bin/toybox_vendor":                "u:object_r:vendor_toolbox_exec:s0",
    "/vendor/bin/toolbox":                      "u:object_r:vendor_toolbox_exec:s0",
    "/vendor/bin/misc_writer":                  "u:object_r:vendor_misc_writer_exec:s0",
    "/vendor/bin/install-recovery.sh":          "u:object_r:vendor_install_recovery_exec:s0",
    "/vendor/bin/vndservicemanager":            "u:object_r:vndservicemanager_exec:s0",
    "/vendor/bin/ot-rcp":                       "u:object_r:ot_rcp_exec:s0",
    # HAL binaries — AOSP vendor/file_contexts: /(vendor|system/vendor)/bin/hw/...
    "/vendor/bin/hw/rild":                      "u:object_r:rild_exec:s0",
    "/vendor/bin/hw/hostapd":                   "u:object_r:hal_wifi_hostapd_default_exec:s0",
    "/vendor/bin/hw/wpa_supplicant":            "u:object_r:hal_wifi_supplicant_default_exec:s0",
    "/vendor/bin/hw/wpa_supplicant_macsec":     "u:object_r:wpa_supplicant_macsec_exec:s0",
    # odm executables — AOSP: /(odm|vendor/odm)/bin/sh
    "/odm/bin/sh":                              "u:object_r:vendor_shell_exec:s0",
    # config files — AOSP: /(vendor|system/vendor)/...
    "/vendor/manifest.xml":                     "u:object_r:vendor_configs_file:s0",
    "/vendor/compatibility_matrix.xml":         "u:object_r:vendor_configs_file:s0",
    "/vendor/etc/cgroups.json":                 "u:object_r:vendor_cgroup_desc_file:s0",
    "/vendor/etc/task_profiles.json":           "u:object_r:vendor_task_profiles_file:s0",
    "/vendor/etc/selinux/vendor_service_contexts": "u:object_r:vendor_service_contexts_file:s0",
    # vendor selinux — AOSP: /(vendor|system/vendor)/etc/selinux/...
    "/vendor/etc/selinux/vendor_file_contexts":                 "u:object_r:file_contexts_file:s0",
    "/vendor/etc/selinux/vendor_mac_permissions.xml":           "u:object_r:mac_perms_file:s0",
    "/vendor/etc/selinux/vendor_property_contexts":             "u:object_r:property_contexts_file:s0",
    "/vendor/etc/selinux/vendor_hwservice_contexts":            "u:object_r:hwservice_contexts_file:s0",
    "/vendor/etc/selinux/vendor_seapp_contexts":                "u:object_r:seapp_contexts_file:s0",
    "/vendor/etc/selinux/vendor_keystore2_key_contexts":        "u:object_r:keystore2_key_contexts_file:s0",
    "/vendor/etc/selinux/vendor_tee_service_contexts":          "u:object_r:tee_service_contexts_file:s0",
    "/vendor/etc/selinux/precompiled_sepolicy":                 "u:object_r:sepolicy_file:s0",
    "/vendor/etc/selinux/precompiled_sepolicy.plat_and_mapping.sha256": "u:object_r:sepolicy_file:s0",
    "/vendor/etc/selinux/nonplat_sepolicy.cil":                 "u:object_r:sepolicy_file:s0",
    "/vendor/etc/selinux/plat_pub_versioned.cil":               "u:object_r:sepolicy_file:s0",
    "/vendor/etc/selinux/vendor_sepolicy.cil":                  "u:object_r:sepolicy_file:s0",
    # ODM selinux — AOSP: /odm/etc/selinux/precompiled_* (hanya /odm, bukan /vendor/odm)
    "/odm/etc/selinux/precompiled_sepolicy":                    "u:object_r:sepolicy_file:s0",
    "/odm/etc/selinux/precompiled_sepolicy.plat_and_mapping.sha256": "u:object_r:sepolicy_file:s0",
    # ODM selinux — AOSP: /(odm|vendor/odm)/etc/selinux/...
    "/odm/etc/selinux/odm_sepolicy.cil":                        "u:object_r:sepolicy_file:s0",
    "/odm/etc/selinux/odm_file_contexts":                       "u:object_r:file_contexts_file:s0",
    "/odm/etc/selinux/odm_seapp_contexts":                      "u:object_r:seapp_contexts_file:s0",
    "/odm/etc/selinux/odm_property_contexts":                   "u:object_r:property_contexts_file:s0",
    "/odm/etc/selinux/odm_service_contexts":                    "u:object_r:vendor_service_contexts_file:s0",
    "/odm/etc/selinux/odm_hwservice_contexts":                  "u:object_r:hwservice_contexts_file:s0",
    "/odm/etc/selinux/odm_keystore2_key_contexts":              "u:object_r:keystore2_key_contexts_file:s0",
    "/odm/etc/selinux/odm_mac_permissions.xml":                 "u:object_r:mac_perms_file:s0",
    "/odm/etc/selinux/odm_tee_service_contexts":                "u:object_r:tee_service_contexts_file:s0",
}

AOSP_VENDOR_PREFIX = [
    # EGL — same_process_hal
    # EGL — AOSP: /(vendor|system/vendor)/lib(64)?/egl(/.*)?  dan /(odm|vendor/odm)/lib(64)?/egl(/.*)?
    ("/vendor/lib64/egl/",                      "u:object_r:same_process_hal_file:s0"),
    ("/vendor/lib/egl/",                        "u:object_r:same_process_hal_file:s0"),
    ("/odm/lib64/egl/",                         "u:object_r:same_process_hal_file:s0"),
    ("/odm/lib/egl/",                           "u:object_r:same_process_hal_file:s0"),
    # VNDK-SP — AOSP: /(vendor|system/vendor)/lib(64)?/vndk-sp(/.*)?  dan /(odm|vendor/odm)/lib(64)?/vndk-sp(/.*)?
    ("/vendor/lib64/vndk-sp/",                  "u:object_r:vndk_sp_file:s0"),
    ("/vendor/lib/vndk-sp/",                    "u:object_r:vndk_sp_file:s0"),
    ("/odm/lib64/vndk-sp/",                     "u:object_r:vndk_sp_file:s0"),
    ("/odm/lib/vndk-sp/",                       "u:object_r:vndk_sp_file:s0"),
    # HAL dir — AOSP: /(vendor|system/vendor)/lib(64)?/hw  dan /(odm|vendor/odm)/lib(64)?/hw
    ("/vendor/lib64/hw/",                       "u:object_r:vendor_hal_file:s0"),
    ("/vendor/lib/hw/",                         "u:object_r:vendor_hal_file:s0"),
    ("/odm/lib64/hw/",                          "u:object_r:vendor_hal_file:s0"),
    ("/odm/lib/hw/",                            "u:object_r:vendor_hal_file:s0"),
    # etc vendor
    ("/vendor/etc/vintf/",                      "u:object_r:vendor_configs_file:s0"),
    ("/vendor/etc/avf/microdroid/",             "u:object_r:vendor_microdroid_file:s0"),
    ("/vendor/etc/aconfig/",                    "u:object_r:vendor_aconfig_storage_file:s0"),
    ("/vendor/etc/",                            "u:object_r:vendor_configs_file:s0"),
    # etc odm — AOSP: /(odm|vendor/odm)/etc(/.*)?
    ("/odm/etc/",                               "u:object_r:vendor_configs_file:s0"),
    # apps — AOSP: /(vendor|system/vendor)/app(/.*)?  dan /(odm|vendor/odm)/app(/.*)?
    ("/vendor/app/",                            "u:object_r:vendor_app_file:s0"),
    ("/vendor/priv-app/",                       "u:object_r:vendor_app_file:s0"),
    ("/odm/app/",                               "u:object_r:vendor_app_file:s0"),
    ("/odm/priv-app/",                          "u:object_r:vendor_app_file:s0"),
    # overlay — AOSP: /(vendor|system/vendor)/overlay(/.*)?  dan /(odm|vendor/odm)/overlay(/.*)?
    ("/vendor/overlay/",                        "u:object_r:vendor_overlay_file:s0"),
    ("/odm/overlay/",                           "u:object_r:vendor_overlay_file:s0"),
    # framework — AOSP: /(vendor|system/vendor)/framework(/.*)?  dan /(odm|vendor/odm)/framework(/.*)?
    ("/vendor/framework/",                      "u:object_r:vendor_framework_file:s0"),
    ("/odm/framework/",                         "u:object_r:vendor_framework_file:s0"),
    # input — AOSP: /(odm|vendor/odm|vendor|system/vendor)/usr/...
    ("/vendor/usr/keylayout/",                  "u:object_r:vendor_keylayout_file:s0"),
    ("/vendor/usr/keychars/",                   "u:object_r:vendor_keychars_file:s0"),
    ("/vendor/usr/idc/",                        "u:object_r:vendor_idc_file:s0"),
    ("/odm/usr/keylayout/",                     "u:object_r:vendor_keylayout_file:s0"),
    ("/odm/usr/keychars/",                      "u:object_r:vendor_keychars_file:s0"),
    ("/odm/usr/idc/",                           "u:object_r:vendor_idc_file:s0"),
    # apex — AOSP regex: /(odm|vendor/odm|vendor|system/vendor)/apex(/[^/]+){0,2}
    ("/vendor/apex/",                           "u:object_r:vendor_apex_file:s0"),
    ("/odm/apex/",                              "u:object_r:vendor_apex_file:s0"),
    # boot ota
    ("/vendor/boot_otas/",                      "u:object_r:vendor_boot_ota_file:s0"),
    # vendor_dlkm / odm_dlkm — AOSP: /(vendor_dlkm|vendor/vendor_dlkm|system/vendor/vendor_dlkm)/etc(/.*)?
    ("/vendor_dlkm/etc/",                       "u:object_r:vendor_configs_file:s0"),
    ("/odm_dlkm/etc/",                          "u:object_r:vendor_configs_file:s0"),

    ("/vendor/",                                "u:object_r:vendor_file:s0"),
    ("/odm/",                                   "u:object_r:vendor_file:s0"),
    ("/vendor_dlkm/",                           "u:object_r:vendor_file:s0"),
    ("/odm_dlkm/",                              "u:object_r:vendor_file:s0"),
    # OEM partition — AOSP: /(oem|vendor/oem)(/.*)?
    ("/oem/overlay/",                           "u:object_r:vendor_overlay_file:s0"),
    ("/oem/",                                   "u:object_r:oemfs:s0"),
]

# Regex vendor — HAL executables (vendor/file_contexts)
# Semua varian path (/vendor dan /system/vendor)
def _hal_regex(pattern_suffix):
    return re.compile(r"^/(?:system/)?vendor/" + pattern_suffix)

AOSP_VENDOR_HAL_REGEX = [
    (_hal_regex(r"bin/hw/android\.hardware\.audio[@.].*service"),           "u:object_r:hal_audio_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.audio\.effect\.service"),       "u:object_r:hal_audio_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.automotive\.audiocontrol[@-]"), "u:object_r:hal_audiocontrol_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.automotive\.can[@-]"),          "u:object_r:hal_can_socketcan_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.automotive\.evs"),              "u:object_r:hal_evs_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.automotive\.ivn"),              "u:object_r:hal_ivn_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.automotive\.vehicle"),          "u:object_r:hal_vehicle_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.automotive\.remoteaccess"),     "u:object_r:hal_remoteaccess_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.atrace"),                       "u:object_r:hal_atrace_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.biometrics\.face"),             "u:object_r:hal_face_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.biometrics\.fingerprint"),      "u:object_r:hal_fingerprint_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.bluetooth@1\.[0-9]+-service\.btlinux"), "u:object_r:hal_bluetooth_btlinux_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.bluetooth"),                    "u:object_r:hal_bluetooth_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.boot"),                         "u:object_r:hal_bootctl_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.broadcastradio"),               "u:object_r:hal_broadcastradio_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.camera"),                       "u:object_r:hal_camera_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.cas"),                          "u:object_r:hal_cas_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.configstore"),                  "u:object_r:hal_configstore_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.confirmationui"),               "u:object_r:hal_confirmationui_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.contexthub"),                   "u:object_r:hal_contexthub_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.drm-service.*clearkey"),        "u:object_r:hal_drm_clearkey_aidl_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.drm"),                          "u:object_r:hal_drm_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.dumpstate"),                    "u:object_r:hal_dumpstate_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.gatekeeper"),                   "u:object_r:hal_gatekeeper_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.gnss"),                         "u:object_r:hal_gnss_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.graphics\.allocator"),          "u:object_r:hal_graphics_allocator_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.graphics\.composer"),           "u:object_r:hal_graphics_composer_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.health\.storage"),              "u:object_r:hal_health_storage_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.health"),                       "u:object_r:hal_health_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.identity"),                     "u:object_r:hal_identity_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.input\.classifier"),            "u:object_r:hal_input_classifier_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.input\.processor"),             "u:object_r:hal_input_processor_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.ir"),                           "u:object_r:hal_ir_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.keymaster"),                    "u:object_r:hal_keymaster_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.security\.keymint"),            "u:object_r:hal_keymint_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.security\.authgraph"),          "u:object_r:hal_authgraph_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.security\.secretkeeper"),       "u:object_r:hal_secretkeeper_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.lights"),                       "u:object_r:hal_light_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.light"),                        "u:object_r:hal_light_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.lowpan"),                       "u:object_r:hal_lowpan_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.macsec"),                       "u:object_r:hal_macsec_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.media\.c2"),                    "u:object_r:mediacodec_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.media\.omx"),                   "u:object_r:mediacodec_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.mediaquality"),             "u:object_r:hal_mediaquality_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.memtrack"),                     "u:object_r:hal_memtrack_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.nfc"),                          "u:object_r:hal_nfc_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.power\.stats"),                 "u:object_r:hal_power_stats_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.power"),                        "u:object_r:hal_power_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.radio\.config"),                "u:object_r:hal_radio_config_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.radio"),                        "u:object_r:hal_radio_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.rebootescrow"),                 "u:object_r:hal_rebootescrow_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.secure_element"),               "u:object_r:hal_secure_element_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.sensors"),                      "u:object_r:hal_sensors_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tetheroffload"),                "u:object_r:hal_tetheroffload_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.thermal"),                      "u:object_r:hal_thermal_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.threadnetwork"),                "u:object_r:hal_threadnetwork_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.trusty"),                       "u:object_r:hal_hwcrypto_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.cec"),                      "u:object_r:hal_tv_cec_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.hdmi\.cec"),                "u:object_r:hal_tv_hdmi_cec_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.hdmi\.connection"),         "u:object_r:hal_tv_hdmi_connection_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.hdmi\.earc"),               "u:object_r:hal_tv_hdmi_earc_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.input"),                    "u:object_r:hal_tv_input_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.tv\.tuner"),                    "u:object_r:hal_tv_tuner_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.usb\.gadget"),                  "u:object_r:hal_usb_gadget_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.usb"),                          "u:object_r:hal_usb_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.uwb"),                          "u:object_r:hal_uwb_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.vibrator"),                     "u:object_r:hal_vibrator_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.virtualization\.capabilities"), "u:object_r:hal_vm_capabilities_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.vr"),                           "u:object_r:hal_vr_default_exec:s0"),
    (_hal_regex(r"bin/hw/android\.hardware\.wifi"),                         "u:object_r:hal_wifi_default_exec:s0"),
    # graphics mapper + gralloc same_process_hal (lib/hw/)
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/hw/android\.hardware\.graphics\.mapper@[0-9]+\.[0-9]+-impl(?:-[0-9]+\.[0-9]+)?\.so$"), "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/hw/android\.hardware\.renderscript@1\.0-impl\.so$"), "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/hw/android\.hidl\.memory@1\.0-impl\.so$"),           "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/hw/gralloc\.default\.so$"),                          "u:object_r:same_process_hal_file:s0"),
    # VNDK-SP: named .so files di lib(64)/ langsung → same_process_hal_file
    # Sumber: vendor/file_contexts — "Libraries removed from vndk-sp" + "Vendor libraries loaded from SP-HAL"
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.graphics\.common(?:-V[0-9]+-ndk|@[0-9]+\.[0-9]+)\.so$"), "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.graphics\.mapper@[0-9]+\.[0-9]+\.so$"),  "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.graphics\.allocator-V[0-9]+-ndk\.so$"),  "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.graphics\.composer3-V[0-9]+-ndk\.so$"),  "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.common(?:-V[0-9]+-ndk|-V[0-9]+\.fmq-V[0-9]+-ndk)\.so$"), "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.common\.fmq-V[0-9]+-ndk\.so$"),          "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hardware\.renderscript@1\.0\.so$"),                "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hidl\.memory(?:\.token)?@1\.0\.so$"),              "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/android\.hidl\.safe_union@1\.0\.so$"),                      "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/lib(?:hwbinder|hidltransport|hidlbase|hidlmemory)\.so$"),    "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/lib(?:RS(?:CpuRef|Driver|_internal)|blas|bcinfo)\.so$"),     "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/lib(?:base|c\+\+|compiler_rt|cutils|dmabufheap|ft2)\.so$"), "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/lib(?:gralloctypes|hardware|ion|jsoncpp|lzma|png)\.so$"),    "u:object_r:same_process_hal_file:s0"),
    (re.compile(r"^/(?:system/)?vendor/lib(?:64)?/lib(?:processgroup|unwindstack|utils(?:callstack)?|z)\.so$"), "u:object_r:same_process_hal_file:s0"),
    # UUID mapping config
    (re.compile(r"^/(?:odm|vendor/odm|(?:system/)?vendor)/etc/hal_uuid_map_.*\.xml$"),                 "u:object_r:vendor_uuid_mapping_config_file:s0"),
    # boringssl vendor
    (re.compile(r"^/(?:system/)?vendor/bin/boringssl_self_test(?:32|64)$"),                            "u:object_r:vendor_boringssl_self_test_exec:s0"),
    # vendor_overlay dari product partition
    (re.compile(r"^/(?:system/)?product/vendor_overlay/[0-9]+/"),                                      "u:object_r:vendor_file:s0"),
]


AOSP_DEV_EXACT = {
    "/dev/ashmem":                  "u:object_r:ashmem_device:s0",
    "/dev/binder":                  "u:object_r:binder_device:s0",
    "/dev/boringssl":               "u:object_r:boringssl_self_test_marker:s0",
    "/dev/console":                 "u:object_r:console_device:s0",
    "/dev/device-mapper":           "u:object_r:dm_device:s0",
    "/dev/eac":                     "u:object_r:audio_device:s0",
    "/dev/event-log-tags":          "u:object_r:runtime_event_log_tags_file:s0",
    "/dev/fuse":                    "u:object_r:fuse_device:s0",
    "/dev/hw_random":               "u:object_r:hw_random_device:s0",
    "/dev/hwbinder":                "u:object_r:hwbinder_device:s0",
    "/dev/ion":                     "u:object_r:ion_device:s0",
    "/dev/keychord":                "u:object_r:keychord_device:s0",
    "/dev/kmsg":                    "u:object_r:kmsg_device:s0",
    "/dev/kmsg_debug":              "u:object_r:kmsg_debug_device:s0",
    "/dev/kvm":                     "u:object_r:kvm_device:s0",
    "/dev/loop-control":            "u:object_r:loop_control_device:s0",
    "/dev/mtp_usb":                 "u:object_r:mtp_device:s0",
    "/dev/null":                    "u:object_r:null_device:s0",
    "/dev/nvhdcp1":                 "u:object_r:video_device:s0",
    "/dev/pmsg0":                   "u:object_r:pmsg_device:s0",
    "/dev/pn544":                   "u:object_r:nfc_device:s0",
    "/dev/port":                    "u:object_r:port_device:s0",
    "/dev/ptmx":                    "u:object_r:ptmx_device:s0",
    "/dev/pvrsrvkm":                "u:object_r:gpu_device:s0",
    "/dev/random":                  "u:object_r:random_device:s0",
    "/dev/rproc_user":              "u:object_r:rpmsg_device:s0",
    "/dev/tty":                     "u:object_r:owntty_device:s0",
    "/dev/tun":                     "u:object_r:tun_device:s0",
    "/dev/uhid":                    "u:object_r:uhid_device:s0",
    "/dev/uinput":                  "u:object_r:uhid_device:s0",
    "/dev/ublk-control":            "u:object_r:ublk_control_device:s0",
    "/dev/urandom":                 "u:object_r:random_device:s0",
    "/dev/usb_accessory":           "u:object_r:usbaccessory_device:s0",
    "/dev/vhost-vsock":             "u:object_r:kvm_device:s0",
    "/dev/vndbinder":               "u:object_r:vndbinder_device:s0",
    "/dev/watchdog":                "u:object_r:watchdog_device:s0",
    "/dev/xt_qtaguid":              "u:object_r:qtaguid_device:s0",
    "/dev/zero":                    "u:object_r:zero_device:s0",
    "/dev/__properties__":          "u:object_r:properties_device:s0",
    "/dev/__properties__/appcompat_override": "u:object_r:properties_device:s0",
    "/dev/__properties__/property_info": "u:object_r:property_info:s0",
    "/dev/__properties__/appcompat_override/property_info": "u:object_r:property_info:s0",
    "/dev/socket/adbd":                     "u:object_r:adbd_socket:s0",
    "/dev/socket/aconfigd_mainline":        "u:object_r:aconfigd_mainline_socket:s0",
    "/dev/socket/aconfigd_system":          "u:object_r:aconfigd_socket:s0",
    "/dev/socket/dnsproxyd":               "u:object_r:dnsproxyd_socket:s0",
    "/dev/socket/dumpstate":               "u:object_r:dumpstate_socket:s0",
    "/dev/socket/fwmarkd":                 "u:object_r:fwmarkd_socket:s0",
    "/dev/socket/lmkd":                    "u:object_r:lmkd_socket:s0",
    "/dev/socket/logd":                    "u:object_r:logd_socket:s0",
    "/dev/socket/logdr":                   "u:object_r:logdr_socket:s0",
    "/dev/socket/logdw":                   "u:object_r:logdw_socket:s0",
    "/dev/socket/statsdw":                 "u:object_r:statsdw_socket:s0",
    "/dev/socket/mdns":                    "u:object_r:mdns_socket:s0",
    "/dev/socket/mdnsd":                   "u:object_r:mdnsd_socket:s0",
    "/dev/socket/prng_seeder":             "u:object_r:prng_seeder_socket:s0",
    "/dev/socket/property_service":        "u:object_r:property_socket:s0",
    "/dev/socket/property_service_for_system": "u:object_r:property_socket:s0",
    "/dev/socket/recovery":                "u:object_r:recovery_socket:s0",
    "/dev/socket/rild":                    "u:object_r:rild_socket:s0",
    "/dev/socket/rild-debug":              "u:object_r:rild_debug_socket:s0",
    "/dev/socket/snapuserd":               "u:object_r:snapuserd_socket:s0",
    "/dev/socket/snapuserd_proxy":         "u:object_r:snapuserd_proxy_socket:s0",
    "/dev/socket/tombstoned_crash":        "u:object_r:tombstoned_crash_socket:s0",
    "/dev/socket/tombstoned_java_trace":   "u:object_r:tombstoned_java_trace_socket:s0",
    "/dev/socket/tombstoned_intercept":    "u:object_r:tombstoned_intercept_socket:s0",
    "/dev/socket/traced_consumer":         "u:object_r:traced_consumer_socket:s0",
    "/dev/socket/traced_perf":             "u:object_r:traced_perf_socket:s0",
    "/dev/socket/traced_producer":         "u:object_r:traced_producer_socket:s0",
    "/dev/socket/heapprofd":               "u:object_r:heapprofd_socket:s0",
    "/dev/socket/uncrypt":                 "u:object_r:uncrypt_socket:s0",
    "/dev/socket/zygote":                  "u:object_r:zygote_socket:s0",
    "/dev/socket/zygote_secondary":        "u:object_r:zygote_socket:s0",
    "/dev/socket/usap_pool_primary":       "u:object_r:zygote_socket:s0",
    "/dev/socket/usap_pool_secondary":     "u:object_r:zygote_socket:s0",
    "/dev/block/by-name/zoned_device":     "u:object_r:zoned_block_device:s0",
    "/dev/dma_heap/system":                "u:object_r:dmabuf_system_heap_device:s0",
    "/dev/dma_heap/system-uncached":       "u:object_r:dmabuf_system_heap_device:s0",
}

AOSP_DEV_PREFIX = [
    ("/dev/adf-interface",          "u:object_r:graphics_device:s0"),
    ("/dev/adf-overlay-engine",     "u:object_r:graphics_device:s0"),
    ("/dev/ashmem",                 "u:object_r:ashmem_libcutils_device:s0"),
    ("/dev/audio",                  "u:object_r:audio_device:s0"),
    ("/dev/block/dm-",              "u:object_r:dm_device:s0"),
    ("/dev/block/ublkb",            "u:object_r:ublk_block_device:s0"),
    ("/dev/block/loop",             "u:object_r:loop_device:s0"),
    ("/dev/block/vold/",            "u:object_r:vold_device:s0"),
    ("/dev/block/ram",              "u:object_r:ram_device:s0"),
    ("/dev/block/zram",             "u:object_r:ram_device:s0"),
    ("/dev/block/",                 "u:object_r:block_device:s0"),
    ("/dev/boringssl/selftest/",    "u:object_r:boringssl_self_test_marker:s0"),
    ("/dev/bus/usb",                "u:object_r:usb_device:s0"),
    ("/dev/cgroup_info/",           "u:object_r:cgroup_rc_file:s0"),
    ("/dev/dma_heap/system-secure", "u:object_r:dmabuf_system_secure_heap_device:s0"),
    ("/dev/dma_heap/",              "u:object_r:dmabuf_heap_device:s0"),
    ("/dev/dm-user/",               "u:object_r:dm_user_device:s0"),
    ("/dev/fscklogs/",              "u:object_r:fscklogs:s0"),
    ("/dev/gnss",                   "u:object_r:gnss_device:s0"),
    ("/dev/graphics/",              "u:object_r:graphics_device:s0"),
    ("/dev/hidraw",                 "u:object_r:hidraw_device:s0"),
    ("/dev/iio:device",             "u:object_r:iio_device:s0"),
    ("/dev/input/",                 "u:object_r:input_device:s0"),
    ("/dev/modem",                  "u:object_r:radio_device:s0"),
    ("/dev/rpmsg-omx",              "u:object_r:rpmsg_device:s0"),
    ("/dev/rtc",                    "u:object_r:rtc_device:s0"),
    ("/dev/snd/",                   "u:object_r:audio_device:s0"),
    ("/dev/socket/ot-daemon/",      "u:object_r:ot_daemon_socket:s0"),
    ("/dev/socket/wpa_eth",         "u:object_r:wpa_socket:s0"),
    ("/dev/socket/wpa_wlan",        "u:object_r:wpa_socket:s0"),
    ("/dev/socket/",                "u:object_r:socket_device:s0"),
    ("/dev/spdif_out",              "u:object_r:audio_device:s0"),
    ("/dev/sys/block/by-name/rootdisk/", "u:object_r:rootdisk_sysdev:s0"),
    ("/dev/sys/block/by-name/userdata/", "u:object_r:userdata_sysdev:s0"),
    ("/dev/sys/fs/by-name/userdata/",    "u:object_r:userdata_sysdev:s0"),
    ("/dev/tty",                    "u:object_r:tty_device:s0"),
    ("/dev/ttyS",                   "u:object_r:serial_device:s0"),
    ("/dev/ttyUSB",                 "u:object_r:usb_serial_device:s0"),
    ("/dev/ttyACM",                 "u:object_r:usb_serial_device:s0"),
    ("/dev/uio",                    "u:object_r:uio_device:s0"),
    ("/dev/v4l-touch",              "u:object_r:input_device:s0"),
    ("/dev/vfio/",                  "u:object_r:vfio_device:s0"),
    ("/dev/video",                  "u:object_r:video_device:s0"),
    ("/dev/",                       "u:object_r:device:s0"),
]

AOSP_DEV_REGEX = [
    (re.compile(r"^/dev/adf[0-9]*$"),           "u:object_r:graphics_device:s0"),
    (re.compile(r"^/dev/block/vd[a-z][0-9]*$"), "u:object_r:vd_device:s0"),
    (re.compile(r"^/dev/cpu_variant:"),          "u:object_r:dev_cpu_variant:s0"),
    (re.compile(r"^/dev/gnss[0-9]+$"),           "u:object_r:gnss_device:s0"),
    (re.compile(r"^/dev/hidraw[0-9]+$"),         "u:object_r:hidraw_device:s0"),
    (re.compile(r"^/dev/iio:device[0-9]+$"),     "u:object_r:iio_device:s0"),
    (re.compile(r"^/dev/rpmsg-omx[0-9]$"),       "u:object_r:rpmsg_device:s0"),
    (re.compile(r"^/dev/rtc[0-9]$"),             "u:object_r:rtc_device:s0"),
    (re.compile(r"^/dev/tty[0-9]*$"),            "u:object_r:tty_device:s0"),
    (re.compile(r"^/dev/ttyS[0-9]*$"),           "u:object_r:serial_device:s0"),
    (re.compile(r"^/dev/ttyUSB[0-9]*$"),         "u:object_r:usb_serial_device:s0"),
    (re.compile(r"^/dev/ttyACM[0-9]*$"),         "u:object_r:usb_serial_device:s0"),
    (re.compile(r"^/dev/uio[0-9]*$"),            "u:object_r:uio_device:s0"),
    (re.compile(r"^/dev/v4l-touch[0-9]*$"),      "u:object_r:input_device:s0"),
    (re.compile(r"^/dev/video[0-9]*$"),          "u:object_r:video_device:s0"),
    (re.compile(r"^/dev/socket/pdx/"),           "u:object_r:socket_device:s0"),
]


AOSP_DATA_PREFIX = [
    ("/data/vendor/wifi/hostapd/",  "u:object_r:hostapd_data_file:s0"),
    ("/data/vendor/wifi/wpa/",      "u:object_r:wpa_data_file:s0"),
]


def lookup_aosp_context(full_path: str) -> str | None:

    p = full_path if full_path.startswith("/") else "/" + full_path
    p_slash = p if p.endswith("/") else p + "/"

    # DATA (/data/vendor/... saja yang AOSP hardcode)
    if p.startswith("/data/"):
        for prefix, ctx in AOSP_DATA_PREFIX:
            if p_slash.startswith(prefix):
                return ctx
        return None

    # DEV (selalu /dev/..., tidak ada prefix partisi)
    if p.startswith("/dev/"):
        ctx = AOSP_DEV_EXACT.get(p)
        if ctx:
            return ctx
        for regex, ctx in AOSP_DEV_REGEX:
            if regex.match(p):
                return ctx
        for prefix, ctx in AOSP_DEV_PREFIX:
            if p_slash.startswith(prefix):
                return ctx
        return None

    # ROOTFS (tidak ada prefix partisi yang dikenal)
    first = p.split("/")[1] if len(p) > 1 else ""
    if first not in ("system", "system_ext", "product", "vendor", "odm",
                     "vendor_dlkm", "odm_dlkm", "oem", "dev"):
        ctx = AOSP_ROOTFS_EXACT.get(p)
        if ctx:
            return ctx
        for regex, ctx in AOSP_ROOTFS_REGEX:
            if regex.match(p):
                return ctx
        for prefix, ctx in AOSP_ROOTFS_PREFIX:
            if p_slash.startswith(prefix):
                return ctx
        return None

    # VENDOR / ODM / OEM
    parts = p.strip("/").split("/")
    is_vendor = parts[0] in ("vendor", "odm", "vendor_dlkm", "odm_dlkm", "oem")

    if is_vendor:
        for regex, ctx in AOSP_VENDOR_HAL_REGEX:
            if regex.search(p):
                return ctx
        ctx = AOSP_VENDOR_EXACT.get(p)
        if ctx:
            return ctx
        for prefix, ctx in AOSP_VENDOR_PREFIX:
            if p_slash.startswith(prefix):
                return ctx
        return None

    # SYSTEM / SYSTEM_EXT / PRODUCT
    for regex, ctx in AOSP_SYSTEM_REGEX:
        if regex.match(p):
            return ctx
    ctx = AOSP_SYSTEM_EXACT.get(p)
    if ctx:
        return ctx
    for prefix, ctx in AOSP_SYSTEM_PREFIX:
        if p_slash.startswith(prefix):
            return ctx

    return None


def is_aosp_hardcoded(full_path: str) -> bool:

    return lookup_aosp_context(full_path) is not None


def get_aosp_context_or_default(full_path: str,
                                 default_system: str = "u:object_r:system_file:s0",
                                 default_vendor: str = "u:object_r:vendor_file:s0") -> tuple[str, bool]:

    ctx = lookup_aosp_context(full_path)
    if ctx:
        return ctx, True

    p = full_path if full_path.startswith("/") else "/" + full_path
    parts = p.strip("/").split("/")
    if parts[0] in ("vendor", "odm", "vendor_dlkm", "odm_dlkm"):
        return default_vendor, False
    return default_system, False


if __name__ == "__main__":
    tests = [
        # rootfs
        ("/apex",                       "u:object_r:apex_mnt_dir:s0"),
        ("/apex/apex-info-list.xml",    "u:object_r:apex_info_file:s0"),
        ("/init",                       "u:object_r:init_exec:s0"),
        ("/mnt",                        "u:object_r:tmpfs:s0"),
        ("/tmp",                        "u:object_r:shell_data_file:s0"),
        ("/linkerconfig",               "u:object_r:linkerconfig_file:s0"),
        # system
        ("/system/bin/linker64",        "u:object_r:system_linker_exec:s0"),
        ("/system/bin/linker",          "u:object_r:system_linker_exec:s0"),
        ("/system/bin/app_process32",   "u:object_r:zygote_exec:s0"),
        ("/system/bin/app_process64",   "u:object_r:zygote_exec:s0"),
        ("/system/bin/vold",            "u:object_r:vold_exec:s0"),
        ("/system/bin/sh",              "u:object_r:shell_exec:s0"),
        ("/system/lib64/libc.so",       "u:object_r:system_lib_file:s0"),
        ("/system/lib/libutils.so",     "u:object_r:system_lib_file:s0"),
        ("/system/lib64/bootstrap/linker64", "u:object_r:system_bootstrap_lib_file:s0"),
        ("/system/etc/selinux/plat_file_contexts", "u:object_r:file_contexts_file:s0"),
        ("/system/apex/com.android.art", "u:object_r:art_apex_dir:s0"),
        ("/system/bin/foo",             "u:object_r:system_file:s0"),   # catch-all
        # system_ext — apex fallback ke system_file, bukan art_apex_dir
        ("/system_ext/apex/com.android.art", "u:object_r:system_file:s0"),
        ("/system_ext/apex/com.foo.bar",     "u:object_r:system_file:s0"),
        ("/system_ext/bin/hwservicemanager", "u:object_r:hwservicemanager_exec:s0"),
        ("/system_ext/lib64/foo.so",         "u:object_r:system_lib_file:s0"),
        ("/system_ext/overlay/foo.apk",      "u:object_r:vendor_overlay_file:s0"),
        # product — apex fallback ke system_file
        ("/product/apex/com.android.art",    "u:object_r:system_file:s0"),
        ("/product/lib64/foo.so",            "u:object_r:system_lib_file:s0"),
        # vendor
        ("/vendor/bin/sh",                   "u:object_r:vendor_shell_exec:s0"),
        ("/vendor/bin/vndservicemanager",     "u:object_r:vndservicemanager_exec:s0"),
        ("/vendor/bin/hw/rild",               "u:object_r:rild_exec:s0"),
        ("/vendor/bin/hw/android.hardware.audio@7.0-service.example", "u:object_r:hal_audio_default_exec:s0"),
        ("/vendor/bin/hw/android.hardware.wifi-service", "u:object_r:hal_wifi_default_exec:s0"),
        ("/vendor/bin/hw/android.hardware.camera.provider@2.0-service", "u:object_r:hal_camera_default_exec:s0"),
        ("/vendor/lib64/egl/libEGL.so",       "u:object_r:same_process_hal_file:s0"),
        ("/vendor/lib64/vndk-sp/libhidlbase.so", "u:object_r:vndk_sp_file:s0"),
        ("/vendor/lib/hw/gralloc.default.so", "u:object_r:vendor_hal_file:s0"),  # /lib/hw/ → vendor_hal_file
        ("/vendor/etc/vintf/manifest.xml",    "u:object_r:vendor_configs_file:s0"),
        ("/vendor/apex/com.android.foo",      "u:object_r:vendor_apex_file:s0"),
        # odm
        ("/odm/bin/sh",                       "u:object_r:vendor_shell_exec:s0"),
        ("/odm/apex/com.foo",                 "u:object_r:vendor_apex_file:s0"),
        # dev
        ("/dev/binder",                       "u:object_r:binder_device:s0"),
        ("/dev/socket/zygote",                "u:object_r:zygote_socket:s0"),
        ("/dev/block/dm-0",                   "u:object_r:dm_device:s0"),
        # negative
        ("/system/bin/my_oem_service",        "u:object_r:system_file:s0"),   # catch-all
    ]

    passed = 0
    failed = 0
    for path, expected in tests:
        result = lookup_aosp_context(path)
        ok = result == expected
        status = "OK" if ok else "FAIL"
        if not ok:
            print(f"[{status}] {path}")
            print(f"   expected: {expected}")
            print(f"   got:      {result}")
            failed += 1
        else:
            passed += 1

    print(f"\nResult: {passed} passed, {failed} failed out of {len(tests)} tests")
