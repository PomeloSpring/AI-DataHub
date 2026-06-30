# Git 操作手册

## 基本配置

```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
git config --list
```

## 仓库初始化

```bash
git init
git clone <远程仓库地址>
```

## 查看状态

```bash
git status                   # 工作区状态
git diff                     # 修改内容
git log                      # 提交历史
git log --oneline            # 简洁模式
git log --oneline -10        # 最近10条
```

## 添加文件

```bash
git add <文件名>             # 添加指定文件
git add .                    # 添加所有修改
git add -A                   # 添加所有文件（含删除）
```

## 提交

```bash
git commit -m "提交说明"
git commit -am "提交说明"    # 跳过暂存区，直接提交已跟踪文件
git commit --amend -m "修改说明"  # 修改最后一次提交
```

## 推送到远程

```bash
git push origin <分支名>
git push origin master       # 推送到主分支
git push -u origin master    # 首次推送并设置上游分支
git push --force origin <分支名>  # 强制推送（慎用）
```

## 拉取远程更新

```bash
git pull origin <分支名>     # 拉取并合并
git fetch origin             # 仅拉取不合并
git merge origin/<分支名>    # 合并远程分支
```

## 分支操作

```bash
git branch                   # 查看本地分支
git branch -a                # 查看所有分支
git branch <分支名>          # 创建分支
git checkout <分支名>        # 切换分支
git checkout -b <分支名>     # 创建并切换分支
git branch -d <分支名>       # 删除已合并分支
git branch -D <分支名>       # 强制删除分支
git merge <分支名>           # 合并分支
```

## 撤销操作

```bash
git checkout -- <文件名>     # 撤销工作区修改
git reset HEAD <文件名>      # 撤销暂存区文件
git reset --soft HEAD~1      # 回退最近一次提交（保留修改）
git reset --hard HEAD~1      # 回退最近一次提交（丢弃修改）
git reset --hard <commit id> # 回退到指定提交
git revert <commit id>       # 撤销指定提交（生成新提交）
```

## 标签操作

```bash
git tag                      # 查看标签
git tag <标签名>             # 创建标签
git tag -a <标签名> -m "说明"  # 创建带说明的标签
git push origin <标签名>     # 推送标签
git push origin --tags       # 推送所有标签
git tag -d <标签名>          # 删除标签
```

## 远程仓库管理

```bash
git remote -v                # 查看远程仓库
git remote add <名称> <地址>  # 添加远程仓库
git remote set-url origin <新地址>  # 修改远程地址
git remote remove <名称>     # 删除远程仓库
```

## 暂存操作

```bash
git stash                    # 暂存当前修改
git stash list               # 查看暂存列表
git stash pop                # 恢复最近暂存
git stash apply stash@{0}    # 恢复指定暂存
git stash drop stash@{0}     # 删除指定暂存
git stash clear              # 清空所有暂存
```

## SSH 配置

```bash
ssh-keygen -t rsa -C "你的邮箱"
cat ~/.ssh/id_rsa.pub
ssh-keyscan -H <主机地址> >> ~/.ssh/known_hosts
ssh -T git@<主机地址>
```

## 常见场景

### 首次提交项目

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin <远程仓库地址>
git push -u origin master
```

### 日常开发提交

```bash
git status
git add .
git commit -m "功能说明"
git push origin master
```

### 创建功能分支

```bash
git checkout -b feature-xxx
git add .
git commit -m "新功能完成"
git push origin feature-xxx
```

### 合并分支

```bash
git checkout master
git pull origin master
git merge feature-xxx
git push origin master
```

### 解决冲突

```bash
git pull origin master
# 手动编辑冲突文件
git add .
git commit -m "解决冲突"
git push origin master
```

## 提交规范

```
<类型>: <说明>
```

常用类型：
- `feat`: 新功能
- `fix`: 修复
- `docs`: 文档
- `style`: 格式
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具

```bash
git commit -m "feat: 添加流式响应功能"
git commit -m "fix: 修复JWT过期问题"
git commit -m "docs: 更新README文档"
```
