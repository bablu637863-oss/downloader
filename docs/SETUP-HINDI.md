# Downloader — आसान setup

## सबसे पहले

[Latest Release](https://github.com/bablu637863-oss/downloader/releases/latest) खोलें। अपने computer के हिसाब से **macOS.zip** या **Windows.zip** डाउनलोड करें और पूरा ZIP extract करें। यह Python वाला desktop tool है, कोई hosted website नहीं।

## Mac

1. Python 3.10+ चाहिए। Python न हो तो [python.org](https://www.python.org/downloads/macos/) से install करें।
2. Extract किए `Downloader` folder में `Setup.command` खोलें। Homebrew हो तो missing FFmpeg/Deno लगाने के लिए script पूछेगी। Homebrew अपने-आप install नहीं किया जाता।
3. Setup पूरा होने पर नई Terminal window में `yt` या `dd` लिखें। `Start.command` से भी खोल सकते हैं।
4. `.command` file की permission न मिले तो उसी folder की Terminal में `chmod +x *.command yt dd` चलाएँ।

## Windows 10 / 11

1. [Python 3.10+](https://www.python.org/downloads/windows/) install करें। Installer में Python launcher / PATH विकल्प उपलब्ध हो तो चालू रखें।
2. Extract किए folder में `Setup.cmd` double-click करें। FFmpeg/Deno missing हों तो setup साफ commands बताएगा; नीचे की commands Windows Terminal में चला सकते हैं:

   ```powershell
   winget install --id Gyan.FFmpeg --exact
   winget install --id DenoLand.Deno --exact
   ```

3. Tools लगाने के बाद पुरानी terminal बंद करके `Setup.cmd` फिर खोलें।
4. नई Terminal में `yt` या `dd` लिखें। `YT` / `DD` भी चलेंगे। `Start.cmd` से भी खोल सकते हैं।
5. SmartScreen/security warning आए तो उसे आँख बंद करके bypass न करें; repository और downloaded file जाँचें। यह unsigned Python source bundle है, signed EXE नहीं।

## Download कैसे करें

- `yt` खोलें → link paste करें → format → quality।
- Thumbnail, trim, filename जैसे options की सूची एक बार आती है। कुछ बदलना हो तो उसका नंबर लिखें; नहीं तो Enter दबाएँ।
- Download शुरू करने के लिए Enter। **Space = Pause / Resume**। **Ctrl+C = pause/cancel विकल्प**।
- Progress में कितना download हुआ / कुल size, speed और time दिखेंगे। Total source size न मिले तो `Unavailable` आएगा।
- पूरा होने पर saved folder दिखाई देगा। Settings में अपना folder, speed limit और cookie browser चुन सकते हैं।

आपकी settings/history आपके computer के `~/.downloader` folder में रहती हैं। GitHub पर upload नहीं होतीं। Browser cookies केवल login वाले content के लिए आपके चुनने पर locally पढ़ी जाती हैं।

## Commands न मिलें तो

नई terminal खोलें। पुराने `yt` aliases हों तो वे पुराने tool को खोल सकते हैं; setup उनको हटाता नहीं है। अपने नए folder का `Start.command` / `Start.cmd` खोलकर सही version इस्तेमाल करें। Folder move/delete करने पर commands टूट सकती हैं; नए स्थान से setup फिर चलाएँ।

[समस्याओं के हल](TROUBLESHOOTING.md) · [Bug report](https://github.com/bablu637863-oss/downloader/issues/new/choose)
