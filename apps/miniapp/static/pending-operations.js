/* A lost response must not turn the next click into a different purchase. */
export function pendingOperations({state,api,t,onRecovered}) {
  let busy=false;
  const root=document.getElementById('pendingOperations');
  const paths=new Set(['/api/v1/storefront/checkout','/api/v1/storefront/wallet/topups','/api/v1/storefront/wallet/topups/method','/api/v1/storefront/wallet/flexible-deposits']);
  const key=()=>`ghbf.pending.${state.bootstrap?.store?.id}.${state.bootstrap?.user?.id}`;
  function read(){try{const raw=sessionStorage.getItem(key());if(!raw)return null;const value=JSON.parse(raw);if(!paths.has(value.path)||!value.payload?.idempotency_key)throw new Error();return value;}catch(_){throw new Error(t('Purchase recovery storage is unavailable. Reopen this store before paying.'));}}
  function save(value){try{if(value)sessionStorage.setItem(key(),JSON.stringify(value));else sessionStorage.removeItem(key());}catch(_){throw new Error(t('Purchase recovery storage is unavailable. Reopen this store before paying.'));}}
  const comparable=payload=>JSON.stringify({...payload,idempotency_key:undefined});
  async function run(path,payload){
    const previous=read();
    if(previous&&(previous.path!==path||comparable(previous.payload)!==comparable(payload)))throw new Error(t('An earlier request needs recovery. Use the recovery panel before making another purchase.'));
    const operation=previous||{path,payload,created_at:new Date().toISOString()};
    save(operation);render();
    try{const result=await api(path,{method:'POST',body:JSON.stringify(operation.payload)});if(!result?.id)throw new Error(t('A previous request has no confirmed response. Recover it using the same request before paying again.'));save(null);render();return result;}
    catch(error){
      // Only definitive request rejection clears the local record. Ambiguous failures keep the same key.
      if(!previous&&(error.status===422||(path.endsWith('/checkout')&&[400,401,403,404].includes(error.status))))save(null);
      render();throw error;
    }
  }
  function render(){
    if(!root)return;
    let pending;try{pending=read();}catch(error){root.classList.remove('hidden');root.textContent=error.message;return;}
    root.replaceChildren();root.classList.toggle('hidden',!pending);if(!pending)return;
    const text=document.createElement('p');text.textContent=t('A previous request has no confirmed response. Recover it using the same request before paying again.');
    const button=document.createElement('button');button.type='button';button.className='secondary-button';button.textContent=t('Recover previous request');button.disabled=busy;
    button.onclick=async()=>{if(busy)return;busy=true;render();try{const result=await run(pending.path,pending.payload);await onRecovered(pending.path,result);}catch(error){const message=document.createElement('p');message.setAttribute('role','alert');message.textContent=error.message;root.append(message);}finally{busy=false;const active=root.querySelector('button');if(active)active.disabled=false;}};
    const reference=document.createElement('p');reference.textContent=`${t('Support reference')}: ${pending.payload.idempotency_key}`;
    root.append(text,reference,button);
  }
  return {run,render};
}
