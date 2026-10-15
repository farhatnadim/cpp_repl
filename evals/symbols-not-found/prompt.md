---
description: "a pasted link error: a library missing from the profile"
tags: [troubleshooting]
max_turns: 50
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

My cpp-repl session (tools/cpp-repl -c .cpp-repl/opencv.toml) fails when I call GaussianBlur:

```
c++> cv::GaussianBlur(img, out, cv::Size(5, 5), 0);
JIT session error: Symbols not found: [ _ZN2cv12GaussianBlurERKNS_11_InputArrayERKNS_12_OutputArrayENS_5Size_IiEEddi ]
error: Failed to materialize symbols: { (main, { __orc_init_func.incr_module_12 }) }
```

What's wrong with my profile, and can you fix it?
