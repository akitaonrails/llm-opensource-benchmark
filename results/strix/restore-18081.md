# Restore the Strix IQ3_S server (port 18081) after our runs

Stopped by llm-opensource-benchmark on 2026-10-26 (user approved: "stop it, run ours first", restore after).
Originally started Sat Oct 24 16:15:51 2026 by the llm-non-coding-benchmark lane.

Restore on 192.168.0.90 (cwd /home/akitaonrails):

    setsid nohup /home/akitaonrails/.local/share/strata/terminal/0.1.45.3/python/bin/python3.12 \
      /home/akitaonrails/.local/share/strata/terminal/0.1.45.3/strata.py serve \
      --config /home/akitaonrails/.local/state/noncoding-benchmark/full-server-20261024.json \
      --offline --shutdown-timeout 1 > /dev/null 2>&1 < /dev/null &

Check: curl -s http://127.0.0.1:18081/v1/models  ->  qwen3.8-flash-next-iq3_s
