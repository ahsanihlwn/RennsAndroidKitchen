# SPDX-License-Identifier: MPL-2.0
"""Conversation-first presentation; raw protocol output stays in local logs."""
import json


LABELS={
 'en':{
  'assistant':'Agent','you':'You','new':'New chat','new_conversation':'New conversation','no_history':'No conversations yet','history':'History','delete':'Delete','close':'Close','more':'Options',
  'workspace_session_reset':'Working folder: {path}. A fresh backend session is used to avoid inheriting another workspace; earlier messages remain in history.',
  'welcome':'What would you like to do?','welcome_body':'Ask a question, or select a file and ask for help.',
  'welcome_hint_context':'Reads selected files & editor lines automatically',
  'welcome_hint_edit':'Edits open buffers & runs Kitchen commands',
  'welcome_hint_commands':'Type / for commands · Drop files to attach',
  'example':'Explain the selected file','placeholder':'Ask anything, or type / for commands','send':'Send','stop':'Stop','latest':'Jump to latest message',
  'stop_after':'Stop agent process','force_stop':'Force stop agent process','agent_stopped':'Agent process stopped. Kitchen tasks already submitted may still be running.','attach':'Attach selection','clear':'Remove attachments','automatic':'Automatic',
  'catalog_unavailable':'Model list unavailable. You can still send using the current or automatic model.','retry':'Retry','catalog_log':'Copy model discovery log path',
  'thinking_level':'Thinking level','effort_unavailable':'Levels not provided by this model','view_usage':'View usage','limits_loading':'Checking usage…','limits_unavailable':'Quota is not reported by this backend.','remaining':'remaining','reset':'Resets','usage_only':'Usage report; account quota is not available.','add_selection':'Attach current selection','add_files':'Attach files…','add_folder':'Attach folder…','attachments':'Attachments',
  'drop_files':'Drop files to attach','attachment_prompt':'See the attached files.','remove_attachment':'Remove attachment','copy_attachment':'Click to select · Ctrl+C to copy',
  'account_limits':'Account limits','usage_details':'Usage details','refresh_usage':'Refresh','quota_window':'Current window','quota_weekly':'Weekly','quota_days':'{n} days','quota_hours':'{n} hours','quota_minutes':'{n} minutes',
  'model':'Model','choose_agent':'Choose an assistant','model_settings':'Choose model','commands':'Commands',
  'thinking':'Thinking · {time}','thought':'Thought for {time}','thinking_details':'Thinking summary','thinking_empty':'The backend is thinking. It has not sent a readable summary yet.','thought_empty':'The backend reported this thinking phase without a readable summary.',
  'refresh':'Refresh installed assistants','log':'Copy diagnostic log location','copy':'Copy',
  'details':'Technical details','hide_details':'Hide details','allow':'Yes','deny':'No','save':'Save','save_help':'Approve and remember for this session','save_unavailable':'This request does not offer a permission to remember',
  'work_live':'Working for {time}','work_done':'Worked for {time}','work_failed':'Failed after {time}','work_stopped':'Stopped after {time}','work_waiting':'Waiting for approval · {time}','work_stopping':'Stopping · {time}',
  'ran':'Ran','analyzed':'Analyzed','edited':'Edited','running_tool':'Running','tool_done':'Completed','tool_failed':'Failed','tool_denied':'Denied','tool_stopped':'Interrupted','tool_unknown':'No result reported for','exit_code':'Exit code',
  'answer':'Send answer','your_answer':'Your answer','choose_answer':'Choose an answer','permission':'Allow this action?',
  'working':'Working on your request…','connecting':'Connecting…','ready':'Ready for your message',
  'attention':'Needs attention','waiting':'Waiting for your answer','stopping':'Waiting for the assistant to stop…',
  'interrupted':'Request stopped','activity':'Activity','activity_failed':'An activity could not finish',
  'activity_working':'Checking your request…','activity_done':'Activity completed','no_reply':'No reply received',
  'permission_body':'The assistant could not run a required action because permission was not granted. No answer was received.',
  'permission_next':'The denied action has not been retried. Check the assistant’s permission settings before requesting a new action.',
  'empty_body':'The assistant ended this request without sending a written reply.',
  'empty_next':'You can send another message or choose another assistant below.',
  'failed_body':'The assistant could not finish this request. Open the details to see what happened.',
  'failed_next':'Check the selected assistant and model before sending another message.',
  'model_error_title':'Model Configuration Error',
  'model_error_next':'Check the selected model or thinking level (effort) below, or switch back to Automatic.',
  'switch_to_automatic':'Switch to Automatic',
  'shortened':'Long output is available in the diagnostic log.',
 },
 'id':{
  'assistant':'Agent','you':'Kamu','new':'Chat baru','new_conversation':'Percakapan baru','no_history':'Belum ada percakapan','history':'Riwayat','delete':'Hapus','close':'Tutup','more':'Pilihan',
  'workspace_session_reset':'Folder kerja: {path}. Sesi backend baru dipakai agar workspace lama tidak ikut terbawa; pesan sebelumnya tetap di riwayat.',
  'welcome':'Mau mengerjakan apa?','welcome_body':'Tanyakan sesuatu, atau pilih file yang ingin dibantu.',
  'welcome_hint_context':'Otomatis membaca file & baris editor yang dipilih',
  'welcome_hint_edit':'Edit buffer langsung & jalankan perintah Kitchen',
  'welcome_hint_commands':'Ketik / untuk perintah · Lepas file untuk lampiran',
  'example':'Jelaskan file yang dipilih','placeholder':'Tanya apa saja, atau ketik / untuk perintah','send':'Kirim','stop':'Hentikan','latest':'Ke pesan terbaru',
  'stop_after':'Hentikan proses agent','force_stop':'Paksa hentikan proses agent','agent_stopped':'Proses agent dihentikan. Tugas Kitchen yang sudah dikirim mungkin masih berjalan.','attach':'Lampirkan pilihan','clear':'Lepas lampiran','automatic':'Otomatis',
  'catalog_unavailable':'Daftar model belum tersedia. Pesan tetap bisa dikirim dengan model aktif atau otomatis.','retry':'Coba lagi','catalog_log':'Salin lokasi log daftar model',
  'thinking_level':'Level berpikir','effort_unavailable':'Model belum menyediakan pilihan level','view_usage':'Lihat penggunaan','limits_loading':'Memeriksa penggunaan…','limits_unavailable':'Backend belum melaporkan kuota.','remaining':'tersisa','reset':'Reset','usage_only':'Laporan penggunaan; kuota akun belum tersedia.','add_selection':'Lampirkan pilihan saat ini','add_files':'Lampirkan file…','add_folder':'Lampirkan folder…','attachments':'Lampiran',
  'drop_files':'Lepas file untuk melampirkan','attachment_prompt':'Lihat lampiran ini.','remove_attachment':'Lepas lampiran','copy_attachment':'Klik untuk memilih · Ctrl+C untuk menyalin',
  'account_limits':'Batas penggunaan akun','usage_details':'Detail penggunaan','refresh_usage':'Perbarui','quota_window':'Periode saat ini','quota_weekly':'Mingguan','quota_days':'{n} hari','quota_hours':'{n} jam','quota_minutes':'{n} menit',
  'model':'Model','choose_agent':'Pilih asisten','model_settings':'Pilih model','commands':'Perintah',
  'thinking':'Berpikir · {time}','thought':'Berpikir selama {time}','thinking_details':'Ringkasan proses berpikir','thinking_empty':'Backend sedang berpikir. Belum ada ringkasan yang dikirim.','thought_empty':'Backend melaporkan fase berpikir ini tanpa ringkasan tertulis.',
  'refresh':'Periksa asisten yang terpasang','log':'Salin lokasi log diagnostik','copy':'Salin',
  'details':'Detail teknis','hide_details':'Tutup detail','allow':'Ya','deny':'Tidak','save':'Simpen','save_help':'Izinkan dan ingat untuk sesi ini','save_unavailable':'Permintaan ini tidak menyediakan izin yang bisa disimpan',
  'work_live':'Bekerja selama {time}','work_done':'Selesai dalam {time}','work_failed':'Gagal setelah {time}','work_stopped':'Dihentikan setelah {time}','work_waiting':'Menunggu izin · {time}','work_stopping':'Menghentikan · {time}',
  'ran':'Ran','analyzed':'Dianalisis','edited':'Diubah','running_tool':'Menjalankan','tool_done':'Selesai','tool_failed':'Gagal','tool_denied':'Ditolak','tool_stopped':'Terputus','tool_unknown':'Belum ada hasil untuk','exit_code':'Kode keluar',
  'answer':'Kirim jawaban','your_answer':'Jawabanmu','choose_answer':'Pilih jawaban','permission':'Izinkan tindakan ini?',
  'working':'Sedang mengerjakan permintaanmu…','connecting':'Menghubungkan…','ready':'Siap menerima pesan',
  'attention':'Perlu perhatian','waiting':'Menunggu jawabanmu','stopping':'Menunggu asisten berhenti…',
  'interrupted':'Permintaan dihentikan','activity':'Aktivitas','activity_failed':'Ada aktivitas yang belum berhasil',
  'activity_working':'Sedang memeriksa permintaan…','activity_done':'Aktivitas selesai','no_reply':'Belum ada jawaban',
  'permission_body':'Asisten tidak bisa menjalankan tindakan yang diperlukan karena izinnya belum diberikan. Belum ada jawaban yang diterima.',
  'permission_next':'Tindakan yang ditolak belum diulang. Periksa pengaturan izin asisten sebelum meminta tindakan baru.',
  'empty_body':'Asisten mengakhiri permintaan ini tanpa mengirim jawaban tertulis.',
  'empty_next':'Kamu bisa mengirim pesan lain atau memilih asisten lain di bawah.',
  'failed_body':'Asisten belum bisa menyelesaikan permintaan ini. Buka detail untuk melihat penyebabnya.',
  'failed_next':'Periksa asisten dan model yang dipilih sebelum mengirim pesan lain.',
  'model_error_title':'Kendala Konfigurasi Model',
  'model_error_next':'Periksa pilihan model atau level berpikir (effort) di bawah, atau beralih ke Otomatis.',
  'switch_to_automatic':'Beralih ke Otomatis',
  'shortened':'Output panjang tersedia di log diagnostik.',
 }
}


def is_model_error(text):
    lowered = str(text).lower()
    return any(k in lowered for k in (
        'invalid model', 'not supported for model', '--effort is not supported',
        'model selection', 'unknown model', 'model not found', 'unsupported model',
        'model catalog', 'rate limit', 'overloaded', 'quota', 'model error'
    ))


def extract_clean_error(text):
    if not text: return ''
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    for line in lines:
        low = line.lower()
        if 'error: ' in low:
            idx = low.find('error: ')
            return line[idx + 7:].strip()
        if 'error:' in low:
            idx = low.find('error:')
            return line[idx + 6:].strip()
        for term in ('invalid model', 'not supported for model', '--effort is not supported'):
            if term in low:
                idx = low.find(term)
                return line[idx:].strip()
    for line in lines:
        low = line.lower()
        if any(term in low for term in ('model', 'effort', 'unsupported', 'invalid', 'quota', 'rate limit', 'usage limit', 'limit reached', 'free usage')):
            if not low.startswith('log:') and not low.startswith('agent exited') and not low.startswith('traceback'):
                return line
    useful = [l for l in lines if not l.startswith('Log:') and not l.startswith('Agent exited') and not l.startswith('Traceback')]
    if useful:
        return useful[0]
    return lines[0] if lines else ''


def labels(language):return dict(LABELS.get(language,LABELS['en']))


def failure_kind(text):
    lowered=text.lower()
    if any(x in lowered for x in ('permission check failed','denied permission','permission denied','denied_actions')):return 'permission_denied'
    if is_model_error(lowered):return 'model_error'
    return 'no_reply'


def duration(seconds):
    seconds=max(0,int(seconds));hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
    return (f'{hours}h ' if hours else '')+(f'{minutes}m ' if minutes else '')+f'{seconds}s'


def tool_entry(row,s,terminal=False):
    status=row.get('status','running');command=row.get('command','');title=command or row.get('path') or row.get('title','Tool')
    failed=status in ('failed','error','ERROR');denied=status in ('declined','denied','rejected')
    active=status in ('running','inProgress','pending','ACTIVE','in_progress')
    completed=status in ('completed','success','DONE','done')
    if terminal and active and not completed and not failed and not denied:status='stopped'
    prefix=s['tool_failed'] if failed else s['tool_denied'] if denied else s['tool_stopped'] if status in ('interrupted','cancelled','stopped') else s['running_tool'] if active and not terminal else s['tool_unknown'] if not completed else s['ran'] if command or row.get('tool_kind') in ('commandExecution','execute','Bash','run_command') else s['tool_done']
    if completed and row.get('path'):
        if row.get('tool_kind') in ('read','Read','view_file','read_file','view_file_outline'):prefix=s['analyzed']
        elif row.get('tool_kind') in ('fileChange','Write','Edit','write_to_file','replace_file_content'):prefix=s['edited']
    detail=row['text']
    if command:detail=command+'\n\n'+detail
    if row.get('cwd'):detail='cwd: '+row['cwd']+'\n\n'+detail
    if row.get('exit_code') is not None:detail+='\n\n'+s['exit_code']+': '+str(row['exit_code'])
    return {'key':row['key'],'title':prefix+' '+title.splitlines()[0][:200],'text':detail[-16000:],'status':status,'failed':failed or denied}


def transcript(messages,language='en'):
    s=labels(language);result=[];turn=[]
    def finish(rows):
        if not rows:return
        tools=[r for r in rows if r['role']=='tool']
        answers=[r for r in rows if r['role']=='assistant' and r['text'].strip()]
        problems=[r for r in rows if r['role']=='problem']
        completed=any(r.get('status') in ('completed','failed') for r in rows if r['role']=='system')
        work=next((r for r in rows if r['role']=='work'),None)
        for r in rows:
            if r['role']=='work' or r['role']=='assistant' and not r['text'].strip():continue
            if r['role']=='tool':
                result.append({**r,**tool_entry(r,s,terminal=bool(work and work.get('status') in ('completed','failed','interrupted'))),'role':'tool'})
                continue
            if r['role']=='thought':
                active=r.get('status')=='thinking'
                result.append({**r,'title':s['thinking' if active else 'thought'].format(time=duration(r.get('elapsed',0))),'thinking':active})
                continue
            if r['role']=='system' and r.get('status') in ('completed','failed','interrupted'):continue
            if r['role']=='problem':
                raw_text = r.get('text', '')
                kind = r.get('failure_kind') or failure_kind(raw_text)
                if kind == 'model_error':
                    clean_msg = extract_clean_error(raw_text) or raw_text
                    result.append({
                        **r,
                        'title': s.get('model_error_title', s['attention']),
                        'body': clean_msg,
                        'next': s.get('model_error_next', s['failed_next']),
                        'is_model_error': True
                    })
                elif kind == 'permission_denied':
                    result.append({
                        **r,
                        'title': s['no_reply'],
                        'body': s['permission_body'],
                        'next': s['permission_next']
                    })
                elif kind == 'no_reply':
                    result.append({
                        **r,
                        'title': s['no_reply'],
                        'body': s['empty_body'],
                        'next': s['empty_next']
                    })
                else:
                    clean_msg = extract_clean_error(raw_text) or s['failed_body']
                    result.append({
                        **r,
                        'title': s['attention'],
                        'body': clean_msg,
                        'next': s['failed_next']
                    })
            else:result.append(dict(r))
        if work:
            state=work.get('status','interrupted')
            key={'completed':'work_done','failed':'work_failed','interrupted':'work_stopped','permission':'work_waiting','stopping':'work_stopping'}.get(state,'work_live')
            result.append({'key':work['key']+':status','role':'status','text':'','title':s[key].format(time=duration(work.get('elapsed',0))),'status':state})
        # Old histories can contain a blank assistant plus a false completion.
        # Explain them on read without changing the original history/log files.
        if completed and not answers and not problems:
            detail='\n\n'.join(r['text'] for r in tools)
            kind=failure_kind(detail)
            result.append({'key':rows[0]['key']+':missing-reply','role':'problem','title':s['no_reply'],
                'body':s['permission_body'] if kind=='permission_denied' else s['empty_body'],
                'next':s['permission_next'] if kind=='permission_denied' else s['empty_next'],'text':detail,'failure_kind':kind})
    for row in messages:
        if row['role']=='user':
            finish(turn);turn=[];result.append(dict(row))
        else:turn.append(row)
    finish(turn)
    return [{**r,'text':r['text'][-16000:],'truncated':r.get('truncated',False) or len(r['text'])>16000} for r in result]
