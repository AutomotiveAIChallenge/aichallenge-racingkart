# 車両PCへSSHするための踏み台構成案

## 目的

手元PCから車両PCへSSHできるようにする。

前提:

- 手元PCはAWS CLI / Session Managerを使ってよい
- 車両PCにはAWS認証情報を置きたくない
- 車両PCはNATやモバイル回線の内側にあり、外部から直接SSHできない想定
- 可能なら、EC2のSSHポートをインターネットへ広く公開したくない

## 重要な整理

車両PCが外部から直接到達できない場合、車両PC自身がどこかへ外向き接続する必要がある。

その外向き接続先には、必ず何らかの入口と認証が必要になる。

つまり、以下をすべて同時に満たす構成は基本的に難しい。

- 車両PCにAWS認証情報を置かない
- Bastionや中継サーバのSSH/TCP入口を一切開けない
- 手元PCから車両PCへSSHしたい

設計上は、次のどれを許容するかを選ぶことになる。

- 車両PCから接続するためのSSH入口をBastionに用意する
- 車両PCにAWS以外のVPN/Zero Trustクライアントを入れる
- 車両PCにもAWS系の認証を持たせる

## 選択肢の比較

| 案 | 車両PCのAWS認証情報 | EC2のSSH公開 | 手元PCの接続 | コメント |
| --- | --- | --- | --- | --- |
| 1. SSM Bastion + 車両PCからreverse SSH | 不要 | 必要。ただし車両PC向けに限定 | SSMでBastionへ入り、Bastion上のreverse tunnelへSSH | AWS認証情報を車両PCに置かずに済む。SSH入口のリスクを制限設定で下げる方式 |
| 2. 車両PCもSSMを使う | 必要 | 不要 | SSM経由 | AWS内の思想としてはきれいだが、車両PCにAWS認証・SSM実行環境が必要 |
| 3. Tailscale / Cloudflare Tunnel / WireGuard等 | 不要 | 不要にできる | 各サービス/VPN経由 | 車両PCにAWS認証を置かず、EC2 SSH公開も避けやすい。組織ポリシー次第 |
| 4. 公開Bastionへ通常SSH | 不要 | 必要 | 手元PCも車両PCもBastionへSSH | 単純だが、SSMを使う理由が薄くなるため非推奨 |

## 案1: SSM Bastion + 車両PCからreverse SSH

構成:

```text
手元PC --SSM--> Bastion EC2 <--reverse SSH-- 車両PC
```

車両PCはAWS CLIやAWS認証情報を持たず、通常のSSHクライアントとしてBastionへ外向き接続する。

車両PC側:

```bash
ssh -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -R 127.0.0.1:10022:localhost:22 \
  cart-tunnel@BASTION_PUBLIC_HOST
```

このとき、Bastion上の `127.0.0.1:10022` が車両PCの `localhost:22` につながる。

手元PC側:

```bash
aws ssm start-session \
  --target i-xxxxxxxxxxxxxxxxx \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["10022"],"localPortNumber":["10022"]}'
```

別ターミナルで:

```bash
ssh -p 10022 vehicle_user@localhost
```

### セキュリティ上の注意

この案では、車両PCがreverse SSHを張るために、BastionのSSH入口が必要になる。

SSMを使う目的が「人間がBastionへ直接SSHしないこと」であれば、この案でも手元PCからBastionへのSSHは不要にできる。

ただし、BastionのSSHポート自体は車両PCから到達可能である必要があるため、次のような制限を入れる。

`/etc/ssh/sshd_config` 例:

```sshconfig
PasswordAuthentication no
PubkeyAuthentication yes

Match User cart-tunnel
    AllowTcpForwarding remote
    GatewayPorts no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PasswordAuthentication no
```

`~cart-tunnel/.ssh/authorized_keys` 例:

```text
no-pty,no-X11-forwarding,no-agent-forwarding,permitlisten="127.0.0.1:10022" ssh-ed25519 AAAA...
```

可能ならSecurity Groupで、Bastionの `22/tcp` は車両PCの送信元IPだけ許可する。

車両PCの送信元IPが固定できない場合は、SSH鍵と専用ユーザーの制限が特に重要になる。

### 向いているケース

- 車両PCにAWS認証情報を置きたくない
- AWS上のBastionを中継点にしたい
- 手元PCからBastionへはSSMで入りたい
- BastionのSSH入口を車両PC向けに限定して許容できる

## 案2: 車両PCもSSMを使う

構成:

```text
手元PC --SSM--> AWS
車両PC --SSM--> AWS
```

EC2のSSHポートを開けなくてよい。

ただし車両PCはAWS SSMへ接続するための認証情報を持つ必要がある。

候補:

- AWS access key
- IAM Roles Anywhere
- AWS IoT証明書を使った認証設計
- その他の一時認証情報払い出し

### 向いているケース

- AWS認証情報を車両PCへ安全に配布・更新・失効できる
- EC2のSSH入口を完全に閉じたい
- AWS標準の監査や権限管理に寄せたい

## 案3: Tailscale / Cloudflare Tunnel / WireGuard等を使う

構成例:

```text
手元PC --VPN/Zero Trust--> 車両PC
```

または:

```text
手元PC --VPN/Zero Trust--> 中継サービス <--車両PC
```

車両PCにAWS認証情報を置かず、EC2のSSHポート公開も避けやすい。

候補:

- Tailscale
- Cloudflare Tunnel
- WireGuard
- Teleport
- AWS Client VPN

### 向いているケース

- AWSだけに閉じる必要がない
- 車両PCに専用クライアントを入れられる
- SSH入口をインターネットに公開したくない
- 運用の簡単さを優先したい

## 案4: 公開Bastionへ通常SSH

構成:

```text
手元PC --SSH--> Bastion <--reverse SSH-- 車両PC
```

単純だが、手元PCからもBastionへ直接SSHするため、SSMを使う利点が小さくなる。

今回の前提では優先度は低い。

## 推奨

現時点の要件では、第一候補は次のどちらか。

1. AWS内に寄せたい場合: `案1: SSM Bastion + 車両PCからreverse SSH`
2. SSHポート公開を避けたい場合: `案3: Tailscale / Cloudflare Tunnel / WireGuard等`

特に「SSMを使う理由はEC2のSSHポートを開けたくないから」という方針が強い場合、案1は思想的に少し後退する。

その場合は、車両PCにAWS認証情報を置かずに済むVPN/Zero Trust系の案3を先に検討するのがよい。

一方で、AWS上のBastionを使いたい、車両PCにはSSH鍵だけ置ければよい、という条件なら案1が現実的。

案1を採用する場合は、BastionのSSHを次のように制限する。

- 車両PC専用ユーザーを使う
- パスワードログインを禁止する
- TTYを禁止する
- remote port forwardingだけ許可する
- listen先を `127.0.0.1:10022` に限定する
- 可能ならSecurity Groupで車両PCの送信元IPに限定する
- 手元PCからBastionへはSSMのみで接続する

