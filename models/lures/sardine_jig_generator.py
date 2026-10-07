import numpy as np, trimesh, manifold3d as m3
L=70.0  # body length, mm (~2.75 in)
N,M=160,64
xs=0.5*(1-np.cos(np.linspace(0,np.pi,N)))*L
u=xs/L
# half-height (Z) profile: narrow tail, widest ~55%, pointed snout
hh=0.95*(1.6+6.0*np.sin(np.pi*np.clip(u,0,1)**0.85)**0.9*(1-0.55*u**3))
hh=np.where(u>0.8,hh*(1-((u-0.8)/0.2)**2*0.75),hh)
# belly deeper than back -> centerline shift down
zc=-1.2*np.sin(np.pi*u)
# half-thickness (Y)
ht=0.68*(1.2+2.6*np.sin(np.pi*u**0.9)**0.7)
th=np.linspace(0,2*np.pi,M,endpoint=False)
e=2.6
cy=np.sign(np.cos(th))*np.abs(np.cos(th))**(2/e)
sz=np.sign(np.sin(th))*np.abs(np.sin(th))**(2/e)
V=[];F=[]
for i,x in enumerate(xs):
    for j in range(M): V.append([x,ht[i]*cy[j],zc[i]+hh[i]*sz[j]])
for i in range(N-1):
    for j in range(M):
        a=i*M+j;b=i*M+(j+1)%M;c=a+M;d=b+M
        F+= [[a,c,b],[b,c,d]]
V.append([xs[0],0,zc[0]]);c0=len(V)-1
V.append([xs[-1],0,zc[-1]]);c1=len(V)-1
for j in range(M):
    F.append([c0,j,(j+1)%M]); k=(N-1)*M
    F.append([c1,k+(j+1)%M,k+j])
body=trimesh.Trimesh(V,F,process=True)
if body.volume<0: body.invert()
assert body.is_watertight
def tom(t): return m3.Manifold(m3.Mesh(vert_properties=np.asarray(t.vertices,np.float32),tri_verts=np.asarray(t.faces,np.uint32)))
B=tom(body)
# line-tie eyelets at both ends (tabs with 2.2 mm hole)
def eyelet(x,z):
    ring=m3.Manifold.cylinder(1.8,3.0,3.0,48).translate([0,0,-0.9])
    hole=m3.Manifold.cylinder(4,1.1,1.1,32).translate([0,0,-2])
    r=(ring-hole).rotate([90,0,0])  # axis along Y
    return r.translate([x,0,z])
B=B+eyelet(L+2.4,zc[-1])+eyelet(-2.4,zc[0])
# raised eyes on both sides near head
ex=L*0.885; ez=zc[np.searchsorted(xs,ex)]+0.6
yi=np.interp(ex,xs,ht)
for s in (1,-1):
    eye=m3.Manifold.sphere(2.4,48).scale([1,0.45,1]).translate([ex,s*(yi-0.3),ez])
    B=B+eye
    pupil=m3.Manifold.sphere(1.0,32).scale([1,0.5,1]).translate([ex,s*(yi+0.75),ez])
    B=B+pupil
mesh=B.to_mesh()
out=trimesh.Trimesh(mesh.vert_properties[:,:3],mesh.tri_verts)
print('watertight',out.is_watertight,'extents',out.extents.round(2),'vol mm3',round(out.volume),
      'lead g',round(out.volume*11.34e-3,1),'zinc g',round(out.volume*7.1e-3,1))
out.export('/home/user/brand-intel/sardine_jig_1oz.stl')
