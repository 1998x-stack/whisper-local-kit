"""End-to-end local API checks using a stub Whisper model (no model download)."""
import os
import tempfile
import time
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app import main

class StubModel:
    def transcribe(self, filename, **kwargs):
        return {'text':'你好世界', 'language':'zh',
                'segments':[{'start':0.2,'end':1.1,'text':'你好世界'}]}

def wav_bytes():
    f=BytesIO()
    with wave.open(f,'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b'\0\0'*32000)
    return f.getvalue()

class APITests(unittest.TestCase):
    def test_local_upload_live_and_batch(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ['WHISPER_BATCH_ROOTS'] = root
            main.DATA_ROOT = Path(root)/'jobsdata'
            main.load_model=lambda name: StubModel()
            main.probe_audio=lambda path: 2.0
            main.jobs.clear()
            with TestClient(main.app) as c:
                self.assertEqual(c.get('/api/health').status_code, 200)
                self.assertEqual(c.get('/assets/sound-gate.js').status_code,200)
                with patch.dict(os.environ, {'WHISPER_HOST_MIC_TOKEN': 'test-token'}):
                    self.assertEqual(c.post('/api/server-mic/start',json={'model':'tiny'}).status_code,403)
                self.assertEqual(c.post('/api/batch', json={'input_directory':root}).status_code,403)
                h={'X-Local-Whisper-Client':'1'}
                resp=c.post('/api/live/sessions',headers=h,json={'model':'tiny','language':'zh'})
                self.assertEqual(resp.status_code,201,resp.text)
                sid=resp.json()['id']
                clip=c.post(f'/api/live/{sid}/chunks', headers=h,
                            data={'offset_seconds':'61.0','duration_seconds':'2.0'},
                            files={'file':('mic.wav',wav_bytes(),'audio/wav')})
                self.assertEqual(clip.status_code,202,clip.text)
                for _ in range(100):
                    status=c.get(f'/api/live/{sid}').json()
                    if status['completed']:break
                    time.sleep(.03)
                self.assertEqual(status['completed'],1,status)
                srt=c.get(f'/api/live/{sid}/download/srt').text
                self.assertIn('00:01:01,200 --> 00:01:02,100',srt)
                self.assertEqual(len(c.get('/api/live/'+sid+'?tail=1').json()['segments']),1)
                gap=c.post(f'/api/live/{sid}/gaps', headers=h,
                           json={'offset_seconds':70.0,'duration_seconds':1.0})
                self.assertEqual(gap.status_code,202,gap.text)
                self.assertIn('未转写',c.get(f'/api/live/{sid}/download/srt').text)
                self.assertEqual(len(c.get(f'/api/live/{sid}?tail=1').json()['segments']),1)
                # Individual live clips are not shown as upload jobs.
                self.assertFalse(any(j.get('kind')=='live' for j in c.get('/api/jobs').json()))
                folder=Path(root)/'audios';folder.mkdir()
                (folder/'lesson.mp3').write_bytes(b'fake')
                batch=c.post('/api/batch',headers=h,json={'input_directory':str(folder)})
                self.assertEqual(batch.status_code,202,batch.text)
                jid=batch.json()['id']
                for _ in range(100):
                    j=c.get(f'/api/jobs/{jid}').json()
                    if j['status'] in ('complete','failed'):break
                    time.sleep(.03)
                self.assertEqual(j['status'],'complete',j)
                self.assertIn('00:00:00,200', (folder/'lesson.srt').read_text())
                # running again with the default skip_existing retains old SRT bytes
                (folder/'lesson.srt').write_text('never overwrite', encoding='utf-8')
                batch2=c.post('/api/batch',headers=h,json={'input_directory':str(folder)})
                jid2=batch2.json()['id']
                for _ in range(100):
                    j2=c.get(f'/api/jobs/{jid2}').json()
                    if j2['status'] in ('complete','failed'):break
                    time.sleep(.03)
                self.assertEqual(j2['skipped_files'],1,j2)
                self.assertEqual((folder/'lesson.srt').read_text(), 'never overwrite')
                with patch('app.main.shutil.which', return_value='/usr/bin/ffprobe'):
                    upload=c.post('/api/transcribe', headers=h,
                                  files={'file':('test.wav',wav_bytes(),'audio/wav')},
                                  data={'model':'tiny','language':'zh','task':'transcribe'})
                self.assertEqual(upload.status_code,202,upload.text)
                uid=upload.json()['id']
                for _ in range(100):
                    uploaded=c.get(f'/api/jobs/{uid}').json()
                    if uploaded['status'] in ('complete','failed'):break
                    time.sleep(.03)
                self.assertEqual(uploaded['status'],'complete',uploaded)
                self.assertIn('real_time_factor',uploaded)
                preview=c.get('/api/jobs').json()
                self.assertTrue(any(j['id']==uid and 'text_preview' in j and 'text' not in j for j in preview))
                self.assertTrue(any(j['id']==uid and '00:00:00,200' in j.get('srt_preview', '') for j in preview))
                response=c.get(f'/api/jobs/{uid}/download/json')
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('unfiltered_segments',response.json())
                srt_response=c.get(f'/api/jobs/{uid}/download/srt')
                self.assertIn('filename="test.srt"',srt_response.headers['content-disposition'])
                saved_srt = main.metadata_path(uid).parent / 'result.srt'
                saved_srt.write_text('1\n00:00:00,200 --> 00:00:01,100\n因為我聽過\n', encoding='utf-8')
                self.assertIn('因为我听过', c.get(f'/api/jobs/{uid}/download/srt').text)
                refreshed = next(j for j in c.get('/api/jobs').json() if j['id'] == uid)
                self.assertIn('因为我听过', refreshed['srt_preview'])
                self.assertIn('因為我聽過', saved_srt.read_text(encoding='utf-8'))
                self.assertEqual(c.get('/api/jobs').headers.get('cache-control'),'no-store')
                # Deletion requires the local request marker.
                self.assertEqual(c.delete(f'/api/jobs/{jid2}').status_code,403)
                self.assertEqual(c.delete(f'/api/jobs/{jid2}',headers=h).status_code,200)
                self.assertEqual(c.get(f'/api/jobs/{jid2}').status_code,404)
                self.assertEqual(c.delete(f'/api/live/{sid}',headers=h).status_code,409)
                self.assertEqual(c.post(f'/api/live/{sid}/stop',headers=h).status_code,200)
                self.assertEqual(c.delete(f'/api/live/{sid}',headers=h).status_code,200)
                self.assertEqual(c.get(f'/api/live/{sid}').status_code,404)

if __name__ == '__main__': unittest.main()
