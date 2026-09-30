/* TEST ONLY: single-UID Work namespace compatibility. Never deploy/preload on a real host.
 * PostgreSQL binaries, transaction engine, WAL and TCP protocol remain unmodified.
 */
#include <unistd.h>
#include <sys/stat.h>
#include <dlfcn.h>
#include <string.h>
uid_t geteuid(void){return 1;}
uid_t getuid(void){return 1;}
int stat(const char *path, struct stat *buf){
 int (*real_stat)(const char*,struct stat*)=dlsym(RTLD_NEXT,"stat");
 int result=real_stat(path,buf);
 if(result==0&&strncmp(path,"/tmp/titan-pgdata",17)==0)buf->st_uid=1;
 return result;
}
