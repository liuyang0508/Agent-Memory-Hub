# 产品影片媒体

V7 影片：160 秒、1920×1080、60 fps，中文旁白与字幕。

- `agent-memory-hub-product-v7.mp4`：已验证的发布原片，SHA-256
  `2088cd66fc3f72cdd214eb1696874d10a15e360a5265851f2db8e86091f17387`。
- `agent-memory-hub-product-v7-poster.webp`：第 6.65 秒画面，供官网播放器使用。
- `agent-memory-hub-product-v7-cover.webp`：同画面加播放入口，供仓库 README 使用。

官网部署将这些版本化文件同步到 `/assets/films/`，素材完整上传后原子替换，再更新首页；重复部署不会截断正在播放的视频文件。
GitHub、Gitee 的中英文 README 引用相同的官网播放器和媒体，不依赖第三方视频平台。
影片仅在访客主动播放时加载，首页不自动下载 MP4 或自动播放声音。

可编辑影片工程与详细标志来源保留在独立制作工程中，本目录只发布成片及展示封面。
