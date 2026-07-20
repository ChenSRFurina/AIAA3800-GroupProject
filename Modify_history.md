# VPet 澶氭ā鎬佹敼閫犱慨鏀瑰巻鍙?

鎸夋椂闂存帓搴忥紝璁板綍鏈粨搴擄紙GroupProject锛変粠澶氭ā鎬佸垵鐗堝埌褰撳墠鍙敤鑱旇皟鐨勪富瑕佹敼鍔紝渚夸簬鏌ラ槄銆?

---

## 2026-07-12

### VPet-Speaking 鍒濈増
- 鏂板 `VPet-Speaking` 鎻掍欢涓庤椋?TTS 鎺ュ叆锛坄61f6f4b9`锛夈€?
- DIY銆岃璇濄€嶈蛋浜戠/鏈湴璇煶鍚堟垚璺緞鐨勫熀纭€楠ㄦ灦銆?

---

## 2026-07-13

### 鏈湴妯″瀷閮ㄧ讲
- 瀹屾垚鏈湴 F5-TTS 绛夋ā鍨嬮儴缃茬浉鍏虫彁浜わ紙`ad6e67d9`锛夈€?

---

## 2026-07-18

### 澶氭ā鎬佹彃浠朵笌鍚庣楠ㄦ灦锛堝垵鐗堝彲杩愯锛?
- 鍚堝叆 Speaking / Gaze / FaceDetect / Audio 澶氭ā鎬佹彃浠躲€佸悗绔笌鎼缓鏂囨。锛坄8b1155f6`锛夈€?
- 绔彛绾﹀畾锛歋peaking `8765` 路 Gaze `8766` 路 FaceDetect `8000` 路 Audio `8010`銆?
- `start-all` / `stop-all` 涓€閿惎鍋滃悗绔笌妗屽疇鍓嶇銆?

### Speaking 鑱旇皟
- DIY 鍥哄畾璋冭瘯鍙?TTS锛涜疆璇?audio `/voice/messages`锛孡LM 鍔╂墜鍥炲鑷姩 TTS锛坄e796f58b`锛夈€?

### FaceDetect 鏈湴 / 杩滅▼鎷嗗垎
- `face-detect-local` 鏈満鎺ㄧ悊锛沗face-detect-remote` + relay锛坄start-all -Remote`锛夛紙`bc8fa262`锛夈€?
- VPet 涓庢祴璇曢〉缁熶竴杩炴湰鏈?`127.0.0.1:8000`銆?

---

## 2026-07-20锛堜粖鏃ワ級

### 涓婂崍鈥撲笅鍗堬細瑙嗙嚎 Gaze 瀹氱涓庡彂鍛嗚璇?
- **瑙嗙嚎绠楁硶**锛歁ediaPipe + solvePnP 澶村Э銆佸ご鐪煎姞鎬ц瀺鍚堛€丷idge 鐢ㄦ埛鏄犲皠銆並alman锛涙湇鍔＄鍏ㄥ睆棰勮涓庝節鐐规牎鍑嗭紱I-DT 璋冭瘯锛堟敞瑙嗙偣鍙樼孩锛夈€?
- **琛屼负鏀归€?*锛堢浉瀵瑰疄鏃惰窡闅忥級锛欼-DT 鍒ゅ畾鐩悓涓€澶勭害 3s 鈫?鎭掗€熻蛋/鐖埌鐩爣 鈫?鎾斁 `GraphType.Move` 鈫?鍒拌揪鍚庝粛鐩潃鍒?`SpeakExternal` 鍙戝憜鍙拌瘝銆?
- 鏂板 `GazeConfig.cs`銆乣IdtFixationDetector.cs`锛涢噸鍐?`GazeTrackingClient` 鐘舵€佹満銆?
- Speaking 鏂板鍏紑鍏ュ彛 `SpeakExternal`锛汻EADME 鏇存柊璁板綍銆?
- 鎻愪氦锛歚79126b37`锛圙aze I-DT + SpeakExternal锛夈€?

### 涓嬪崍锛欰udio 闀挎湡璁板繂涓庝汉璁?
- `character_setting.py`锛氬垎灞傝蹇嗐€丳ersona锛沗main.py` 娉ㄥ叆 system prompt銆?
- `.gitignore` 蹇界暐鐢ㄦ埛璁板繂 JSON锛屼繚鐣?`memory/.gitkeep`銆?
- 鎻愪氦锛歚32beed1e`銆?

### 涓嬪崍锛欶aceDetect 鎯呯华闄即 鈫?LLM 鈫?Speaking
- FaceDetect 杞 `GET /latest`锛涢潪 Neutral 鎯呯华锛圚appy/Sad/Surprise/Fear/Disgust/Anger锛夊強鐤插姵瑙﹀彂 `POST /chat/care`銆?
- `care_prompts.py`锛氭瀹犵煭鍙ｈ绾︽潫锛堢鏃佺櫧/灏忚浣擄級锛涗笉鍚堟牸璧版湰鍦?fallback銆?
- **杈规部瑙﹀彂**锛氭儏缁嚭鐜?鍒囨崲璇翠竴娆★紝鍚屼竴鎯呯华杩炵画涓嶉噸澶嶏紱鍥炲埌 Neutral 鍚庡啀鍑虹幇鍙啀璇淬€?
- `/chat/care` 娉ㄥ叆璇煶闀挎湡璁板繂涓婁笅鏂囷紱璁板繂**浠?*璁板綍璇煶杞啓鐢ㄦ埛璇濓紙鏍煎紡 `鐢ㄦ埛璇达細鈥锛夛紝涓嶈惤鐩?care/HTTP 璋冭瘯銆?

### FaceDetect 涓庢憚鍍忓ご鑱旇皟
- `start-all` 鍚姩鍚庤嚜鍔ㄦ墦寮€娴嬭瘯椤?`http://127.0.0.1:8000/test-frontend/`銆?
- Gaze 鍏变韩甯э細`GET /camera/jpeg`锛汧aceDetect 榛樿 `FACE_USE_GAZE_CAMERA=1` 浠?Gaze 鎷夊抚锛?*鍙笉鍏宠绾?*銆?
- 娴嬭瘯椤典紭鍏?Gaze 鍏变韩锛沗?localcam=1` 寮哄埗鏈満鎽勫儚澶达紱鎽勫儚澶撮敊璇腑鏂囨彁绀恒€?
- local/remote `server.py`銆乣relay.py` 澧炲姞 `/latest`銆?

### 淇涓庣ǔ瀹氭€?
- `SpeakExternal` 寮哄埗鍥?UI 绾跨▼锛汫aze 鍒拌揪鐩爣榛樿蹇呰鍙戝憜鍙拌瘝锛堥伩鍏嶈蛋鍔ㄥ悗瑙嗙嚎椋樿蛋涓嶈璇濓級銆?
- FaceDetect 鎻掍欢绂佹鎶婂師鐢?OpenCv DLL 鏀捐繘 `plugin/`锛圴Pet `LoadFrom` 浼?BadImageFormat锛夈€?
- `/latest` JSON **snake_case** 鍙嶅簭鍒楀寲淇锛堟鍓?`faces_count` 璇诲け璐ュ鑷存案涓嶈皟 LLM锛夈€?
- 鎻掍欢鍔犺浇鍚庣害 4s 鑷姩鍚姩鎯呯华闄即锛汸ersonaConfig dataclass 榛樿鍊艰娉曚慨澶嶃€?
- 闄即 prompt 鏀剁揣锛氱煭鍙ャ€佺涓€浜虹О銆佺绗笁浜虹О鏃佺櫧銆?

### 鏂囨。涓庤剼鏈?
- 鏍圭洰褰?`README.md`锛氫汉鑴搁櫔浼淬€丟aze 鍏变韩鎽勫儚澶磋鏄庛€?
- `VPet-FaceDetect/README.md`銆乣VPet-Speaking/README.md` 鏇存柊璁板綍銆?
- `start-all.bat` / `start-all.ps1`锛歚-NoFaceBrowser`銆丟aze 鎷夊抚鐜鍙橀噺銆?

---

## 褰撳墠鑱旇皟璺緞锛堟憳瑕侊級

```text
Gaze :8766 鈹€鈹€camera/jpeg鈹€鈹€鈻?FaceDetect :8000 鈹€鈹€/latest鈹€鈹€鈻?VPet-FaceDetect
                              鈹?                             鈹?
                              鈹斺攢 /chat/care 鈼勨攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
                                     鈹?
                              Audio :8010 (璁板繂+鐭彛璇?LLM)
                                     鈹?
                              VPet-Speaking (F5/讯飞 TTS)
```

瑙嗙嚎鍙戝憜锛欸aze I-DT 鈫?绉诲姩 鈫?`SpeakExternal`锛堝浐瀹氬彂鍛嗗彞锛屼笉缁?LLM锛夈€? 
鎯呯华闄即锛氶潪 Neutral 杈规部 鈫?`/chat/care`锛堝彲甯﹁蹇嗭級鈫?`SpeakExternal`銆?
