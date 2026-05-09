# Local Secrets

`local/secrets/` は、ローカル専用の秘密情報を置くための予備領域です。

通常は、まず `.env`、OSのsecret store、または外部サービス側のsecret管理を使います。  
それで足りない場合だけ、このディレクトリを使います。

秘密値は、commit、log出力、要約、memory candidate へのコピーをしてはいけません。
