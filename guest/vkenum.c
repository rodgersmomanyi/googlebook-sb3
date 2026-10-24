#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <hardware/hardware.h>
#include <hardware/hwvulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <android/log.h>

static int kfd=-1;
static void emit(const char* s){
  __android_log_print(ANDROID_LOG_ERROR,"VKENUM","%s",s);
  if(kfd>=0){ char b[700]; int n=snprintf(b,sizeof b,"VKENUM %s\n",s); if(n>0) write(kfd,b,(size_t)n); }
}
static void devprops(const char* label, PFN_vkGetInstanceProcAddr gipa, VkInstance inst, uint32_t n){
  char b[600];
  PFN_vkEnumeratePhysicalDevices epd=(PFN_vkEnumeratePhysicalDevices)gipa(inst,"vkEnumeratePhysicalDevices");
  PFN_vkGetPhysicalDeviceProperties gpp=(PFN_vkGetPhysicalDeviceProperties)gipa(inst,"vkGetPhysicalDeviceProperties");
  if(!n) return;
  VkPhysicalDevice pd[8]; if(n>8)n=8; epd(inst,&n,pd);
  for(uint32_t i=0;i<n;i++) if(gpp){VkPhysicalDeviceProperties p; memset(&p,0,sizeof p); gpp(pd[i],&p);
    snprintf(b,sizeof b,"%s dev[%u]=\"%s\" type=%d",label,i,p.deviceName,p.deviceType); emit(b);}
}
int main(void){
  kfd=open("/dev/kmsg",O_WRONLY|O_CLOEXEC);
  emit("VKENUM_START");
  void* D=dlopen("/vendor/lib64/hw/vulkan.intel.so",RTLD_NOW);
  char b[600];
  if(!D){snprintf(b,sizeof b,"HMI dlopen_FAIL %s",dlerror()); emit(b); emit("VKENUM_DONE"); return 0;}
  hwvulkan_module_t* mod=(hwvulkan_module_t*)dlsym(D,"HMI");
  if(!mod){emit("HMI symbol NOT FOUND"); emit("VKENUM_DONE"); return 0;}
  snprintf(b,sizeof b,"HMI tag=0x%x module_api=0x%x hal_api=0x%x id=%s methods=%p open=%p",
    mod->common.tag, mod->common.module_api_version, mod->common.hal_api_version,
    mod->common.id?mod->common.id:"(null)", (void*)mod->common.methods,
    mod->common.methods?(void*)mod->common.methods->open:(void*)0); emit(b);
  if(!mod->common.methods||!mod->common.methods->open){emit("HMI no open method"); emit("VKENUM_DONE"); return 0;}
  hwvulkan_device_t* dev=NULL;
  int rc=mod->common.methods->open(&mod->common, HWVULKAN_DEVICE_0, (hw_device_t**)&dev);
  snprintf(b,sizeof b,"HMI open() rc=%d dev=%p",rc,(void*)dev); emit(b);
  if(rc!=0||!dev){emit("VKENUM_DONE"); return 0;}
  snprintf(b,sizeof b,"HMI dev: CreateInstance=%p GIPA=%p EnumInstExt=%p",
    (void*)dev->CreateInstance,(void*)dev->GetInstanceProcAddr,(void*)dev->EnumerateInstanceExtensionProperties); emit(b);
  if(!dev->CreateInstance||!dev->GetInstanceProcAddr){emit("HMI dev missing fn ptrs"); emit("VKENUM_DONE"); return 0;}
  VkApplicationInfo ai; memset(&ai,0,sizeof ai); ai.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO; ai.apiVersion=VK_API_VERSION_1_1;
  VkInstanceCreateInfo ci; memset(&ci,0,sizeof ci); ci.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO; ci.pApplicationInfo=&ai;
  VkInstance inst=VK_NULL_HANDLE; VkResult r=dev->CreateInstance(&ci,NULL,&inst);
  snprintf(b,sizeof b,"HMI dev->CreateInstance result=%d inst=%p",(int)r,(void*)inst); emit(b);
  if(r!=VK_SUCCESS||!inst){emit("VKENUM_DONE"); return 0;}
  PFN_vkEnumeratePhysicalDevices epd=(PFN_vkEnumeratePhysicalDevices)dev->GetInstanceProcAddr(inst,"vkEnumeratePhysicalDevices");
  if(!epd){emit("HMI GIPA(vkEnumeratePhysicalDevices)=NULL"); emit("VKENUM_DONE"); return 0;}
  uint32_t n=0; VkResult r2=epd(inst,&n,NULL);
  snprintf(b,sizeof b,"HMI enumerate result=%d count=%u",(int)r2,n); emit(b);
  devprops("HMI",dev->GetInstanceProcAddr,inst,n);
  emit("VKENUM_DONE");
  if(kfd>=0) close(kfd);
  return 0;
}
