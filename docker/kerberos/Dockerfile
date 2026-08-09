FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    krb5-kdc \
    krb5-admin-server \
    && rm -rf /var/lib/apt/lists/*

ENV KRB5_REALM=EXAMPLE.COM
ENV KRB5_KDC=localhost
ENV KRB5_ADMIN_SERVER=localhost
ENV KRB5_MASTER_PASSWORD=admin123

COPY krb5.conf /etc/krb5.conf

RUN mkdir -p /var/kerberos/krb5kdc /var/kerberos/adata

COPY kdc.conf /var/kerberos/krb5kdc/kdc.conf
COPY kadm5.acl /var/kerberos/krb5kdc/kadm5.acl

RUN kdb5_util create -s -P ${KRB5_MASTER_PASSWORD} && \
    kadmin.local -q "addprinc -pw admin123 admin/admin@${KRB5_REALM}" && \
    kadmin.local -q "addprinc -pw password123 zhangsan@${KRB5_REALM}" && \
    kadmin.local -q "addprinc -pw password123 lisi@${KRB5_REALM}"

EXPOSE 88 749

CMD ["sh", "-c", "krb5kdc && kadmind -nofork"]
