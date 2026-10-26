README.md 已产出，结构如下：

安装依赖：后端执行 pip install -r requirements.txt，前端执行 npm install；.env 中的四项配置和演示账号都写在文件里。

三条可直接复制的命令

cd backend && python -m app.init_db
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev

这三条命令与接口路径、端口、演示账号 admin@example.com / admin123 保持一致；另附一段 API 示例（取 token → 建待办 → 按月导出），TOKEN 的获取方式也一并给出。

CSV 列名对照表：共 9 列——id / title / status（todo·doing·done）/ priority（low·medium·high）/ due_date / estimate_hours / owner_email / created_at / completed_at，每列都有格式约束和示例值。工时列采用定点两位小数（Decimal 序列化），响应精度按上游结论固定保留到分，不会出现 1.4999999 这类浮点尾数。

常见问题共 6 条，覆盖建表缺失（no such table: users）、CORS 被拦（FRONTEND_ORIGIN 端口不一致须重启）、token 过期 401、端口占用、Excel 中文乱码，以及 1 月切换“上个月”越界返回 400（月份限定 2000-01 至当前月，前端已禁用该按钮）。

需要我把 README 里的命令与真实代码再核对一遍的话，把后端目录贴出来即可。

README.md
4 KB
查看所有产物 (1)