export async function api<T>(path:string,body?:unknown,method=body?'POST':'GET'):Promise<T>{
  const response=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});
  const result=await response.json();
  if(!response.ok)throw new Error([result.error,...(result.details||[])].join(' · '));
  return result;
}
